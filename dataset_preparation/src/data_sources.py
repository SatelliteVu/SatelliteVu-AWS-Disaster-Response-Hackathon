import json
import os
from datetime import datetime
from zipfile import ZipFile

import affine
import geopandas as gpd
import pandas as pd
import pyproj
import rasterio
import requests
import xarray as xarr
from geocube.api.core import make_geocube
from pyproj import CRS
from rasterio.enums import Resampling
from shapely.geometry import mapping, MultiPoint, shape
from shapely.ops import transform, nearest_points
from shapely.ops import transform as shapely_tf
from sklearn.cluster import DBSCAN

from src.constants import DEFAULT_PARAMS, CHIP_SIZE
from src.geospatial import (
    build_vrt,
    buffer_point,
    reproject_coordinates,
    bounds_to_geojson,
    read_geospatial_file,
)


def unzip_csvs(zip_file):
    """
    Unzip csvs from a file
    :param zip_file: path of zip file to unzip
    :return: list of unzipped csv filepaths
    """
    unzipped = []
    with ZipFile(zip_file, "r") as zip_obj:
        for zipped_file in zip_obj.namelist():
            if zipped_file.endswith(".csv"):
                zip_obj.extract(zipped_file, zip_file.parent)
                unzipped.append(zip_file.parent.joinpath(zipped_file))
    return unzipped


def cluster_fires(fire_dataframe, min_cluster_points=25):
    """
    Given a geodataframe of fire points, for each date, create clusters
    :param fire_dataframe: geodataframe of fire points
    :param min_cluster_points: minimum number of fire points in a cluster for it to be kept
    :return: geodataframe of fire points that belong to a cluster
    """
    clustered_fires_for_dates = []
    number_of_clusters = 0
    for date in fire_dataframe["acq_date"].unique().tolist():
        fires_for_date = fire_dataframe[fire_dataframe["acq_date"] == date]
        fire_clusters = DBSCAN(eps=0.01, min_samples=1).fit(
            fires_for_date[["longitude", "latitude"]].values
        )
        # add clusters label
        cluster_labels = fire_clusters.labels_ + number_of_clusters
        number_of_clusters += fire_clusters.labels_.max()

        cluster_labels = pd.Series(cluster_labels, name="label")
        # shift to match date selection
        cluster_labels.index += fires_for_date.index.min()
        clustered_fires_for_dates.append(
            pd.concat([fires_for_date, cluster_labels], axis=1)
        )

    clustered_fires = pd.concat(clustered_fires_for_dates)
    # drop clusters with < min_cluster_points
    label_counts = clustered_fires["label"].value_counts()
    clustered_fires["label"] = clustered_fires["label"].apply(
        lambda x: x if label_counts[x] >= min_cluster_points else -1
    )
    clustered_fires = clustered_fires[clustered_fires["label"] != -1]

    # reset label to be continuous
    clustered_fires['label'] = clustered_fires.groupby('label').ngroup()
    return clustered_fires


def create_chip_bounds(clustered_fires):
    """
    Given a geodataframe of clustered fire points create chip bbox and save metadata to csv
    :param clustered_fires: geodataframe of clustered fire points
    :param output_fp: directory for output file to be written to
    :return: filepath of the records.csv created/updated
    """
    chip_bounds = []
    for cluster in clustered_fires["label"].unique().tolist():
        clustered_fire = clustered_fires[clustered_fires["label"] == cluster]
        date = clustered_fire["acq_date"].values[0]
        multipoint_fire_feature = MultiPoint([x for x in clustered_fire.geometry])
        # convert to polygon & get centre
        multipoint_fire_feature_centre = multipoint_fire_feature.convex_hull.centroid
        # get closest point to centre
        central_fire_point = nearest_points(
            multipoint_fire_feature, multipoint_fire_feature_centre
        )[0]

        # build bbox around the clusters central fire point
        bbox_4326, utm_crs = buffer_point(
            central_fire_point, buffer_m=15750, output_4326=True
        )
        bbox_4326_geojson = json.dumps(
            mapping(transform(lambda x, y: (y, x), bbox_4326))
        )
        chip = make_geocube(
            vector_data=gpd.GeoDataFrame(
                geometry=[central_fire_point], crs="EPSG:4326"
            ),
            resolution=(-500, 500),
            output_crs=utm_crs,
            geom=bbox_4326_geojson,
        )

        chip_bounds.append([cluster, *chip.rio.bounds(), utm_crs.to_epsg(), date])

    return pd.DataFrame(
        chip_bounds, columns=["idx", "left", "bottom", "right", "top", "epsg", "date"]
    )


def read_modis_bands(results, bands, epsg, topleft, aoi):
    """
    Read the modis bands with the appropriate transformation for chip creation
    :param results: STAC query results
    :param bands: List of Modis bands to read
    :param epsg: integer EPSG code
    :param topleft: List of [top, left] coordinates in utm zone
    :param aoi: the area to clip
    :return: The stacked bands as an array
    """
    dst_crs = CRS.from_epsg(epsg)
    dst_transform = affine.Affine(500, 0.0, topleft[1], 0.0, -500, topleft[0])
    band_data = []

    # If we have multiple images for the query, if a chip spans multiple MODIS tiles, we need to mosaic them
    if len(results) == 1:
        for band in bands:
            filepath = results[0]["assets"][band]["href"]
            with rasterio.open(filepath) as src:
                data, tf = read_geospatial_file(aoi, dst_crs, dst_transform, src)
            band_data.append(data)
    else:
        for idx, band in enumerate(bands):
            filepaths = [
                i["assets"][band]["href"].replace("s3://", "/vsis3/") for i in results
            ]
            vrt_path = build_vrt(filepaths)
            with rasterio.open(vrt_path) as src:
                data, tf = read_geospatial_file(aoi, dst_crs, dst_transform, src)
            band_data.append(data)
            os.remove(vrt_path)
    return band_data


def fires_from_topleft(top_left, epsg_code, date_to_query, fires):
    """
    Given input chip parameters, load fire data and rasterize the points
    :param top_left: list of the top left coordinates of the chip
    :param epsg_code: EPSG code for top_left
    :param date_to_query: date of the fire data to load
    :param fires : gpd.GeoDataFrame or filename
    :return: xarray.Dataset containing rasterized fire points
    """
    aoi = bounds_to_geojson(
        rasterio.coords.BoundingBox(
            left=top_left[1],
            right=top_left[1] + 32000,
            bottom=top_left[0] - 32000,
            top=top_left[0],
        )
    )
    # reproj the bbox from utm to 4326
    utm_to_wgs84_transformer = pyproj.Transformer.from_crs(
        epsg_code, 4326, always_xy=True
    ).transform
    aoi_wgs84 = shapely_tf(utm_to_wgs84_transformer, shape(aoi))

    # load fire data intersecting chip bbox
    if isinstance(fires, str):
        fires_in_chip = gpd.read_file(fires, layer="merge", bbox=aoi_wgs84)
    else:
        chip_poly = gpd.GeoDataFrame(geometry=[aoi_wgs84], crs="EPSG:4326")
        fires_in_chip = fires[fires["acq_date"] == date_to_query].clip(chip_poly)

    fires_in_chip = fires_in_chip[fires_in_chip["acq_date"] == date_to_query]

    if fires_in_chip.empty:
        # possible if fire dies "next day"
        fires_in_chip = gpd.GeoDataFrame(geometry=[aoi_wgs84.centroid], crs="EPSG:4326")
        fires_in_chip["bool"] = 0
        fires_in_chip["frp"] = 0
    else:
        fires_in_chip["bool"] = 1
        fires_in_chip["frp"] = pd.to_numeric(fires_in_chip["frp"])

    bbox_4326, utm_crs = buffer_point(
        aoi_wgs84.centroid, buffer_m=15750, output_4326=True
    )
    bbox_4326_geojson = json.dumps(mapping(transform(lambda x, y: (y, x), bbox_4326)))

    # rasterize
    fire_array = make_geocube(
        vector_data=fires_in_chip,
        measurements=["bool", "frp"],
        resolution=(-500, 500),
        output_crs=epsg_code,
        fill=0,
        geom=bbox_4326_geojson,
    )
    return fire_array


def elevation_from_topleft(top_left, epsg, cog_footprints):
    """
    Given input chip parameters, load elevation data and reproject to the chip CRS
    :param top_left: list of the top left coordinates of the chip
    :param epsg_code: EPSG code for top_left
    :param cog_footprints: gpd.GeoDataFrame of the dem footprints
    :return: numpy array of the elevation data
    """
    aoi = bounds_to_geojson(
        rasterio.coords.BoundingBox(
            left=top_left[1],
            right=top_left[1] + 32000,
            bottom=top_left[0] - 32000,
            top=top_left[0],
        )
    )

    aoi_4326 = reproject_coordinates(aoi, epsg, 4326)
    cog_intersections = cog_footprints[cog_footprints.intersects(shape(aoi_4326))]
    dem_root = "/vsis3/copernicus-dem-30m"
    file_paths = []
    for cog_filename in cog_intersections.id:
        file_paths.append(f"{dem_root}/{cog_filename}/{cog_filename}.tif")

    vrt_path = build_vrt(file_paths)

    with rasterio.open(vrt_path) as src:
        dst_crs = CRS.from_epsg(epsg)
        dst_transform = affine.Affine(500, 0.0, top_left[1], 0.0, -500, top_left[0])
        elevation_data, tf = read_geospatial_file(aoi, dst_crs, dst_transform, src)
        os.remove(vrt_path)
    return elevation_data[0]


def landcover_from_topleft(top_left, epsg):
    """
    Given input chip parameters, load landcover data and reproject to the chip CRS
    :param top_left: list of the top left coordinates of the chip
    :param epsg_code: EPSG code for top_left
    :return: numpy array of the landcover data
    """
    aoi = bounds_to_geojson(
        rasterio.coords.BoundingBox(
            left=top_left[1],
            right=top_left[1] + 32000,
            bottom=top_left[0] - 32000,
            top=top_left[0],
        )
    )

    with rasterio.open(
        "s3://esa-worldcover/v100/2020/ESA_WorldCover_10m_2020_v100_Map_AWS.vrt"
    ) as src:
        dst_crs = CRS.from_epsg(epsg)
        dst_transform = affine.Affine(500, 0.0, top_left[1], 0.0, -500, top_left[0])
        landcover_data, tf = read_geospatial_file(aoi, dst_crs, dst_transform, src)
    return landcover_data[0]


def ndvi_from_topleft(top_left, epsg, date_to_query):
    """
    Given input chip parameters, load MODIS MCD43A4 data, reproject to the chip CRS and calculate NDVI
    :param top_left: list of the top left coordinates of the chip
    :param epsg_code: EPSG code for top_left
    :param date_to_query: date to load data for as string '2021-05-01'
    :return: numpy array of the NDVI data derived from MODIS bands
    """
    date_to_query = datetime.strptime(date_to_query, "%Y-%m-%d")
    aoi = bounds_to_geojson(
        rasterio.coords.BoundingBox(
            left=top_left[1],
            right=top_left[1] + 32000,
            bottom=top_left[0] - 32000,
            top=top_left[0],
        )
    )
    aoi_4326 = reproject_coordinates(aoi, epsg, 4326)

    results_from_astrea_stac = requests.post(
        f"https://eod-catalog-svc-prod.astraea.earth/search",
        json={
            "intersects": aoi_4326,
            "collections": ["mcd43a4"],
            "datetime": f"{date_to_query.strftime('%Y-%m-%d')}T00:00:00Z/{date_to_query.strftime('%Y-%m-%d')}T23:59:59Z",
            "limit": 100,
        },
    )

    # Update the results to point to the non requester-pays bucket
    results_updated_links = results_from_astrea_stac.json()["features"].copy()
    for i in range(len(results_from_astrea_stac.json()["features"])):
        for band in list(
            results_from_astrea_stac.json()["features"][i]["assets"].keys()
        ):
            results_updated_links[i]["assets"][band][
                "href"
            ] = results_from_astrea_stac.json()["features"][i]["assets"][band][
                "href"
            ].replace(
                "astraea-opendata", "modis-pds"
            )

    bands = read_modis_bands(results_updated_links, ["B01", "B02"], epsg, top_left, aoi)
    ndvi = (bands[1] - bands[0]) / (bands[1] + bands[0])
    return ndvi[0]


def atmospheric_from_topleft(topleft, epsg_code, date, params):
    """
    Given input chip and desired era5 variables, load data, reproject to the chip CRS and resample
    :param sample: chip parameters
    :param params: list of era5 variables to load
    :return: xarray.Dataset of atmospheric data
    """
    date_to_query = datetime.strptime(date, "%Y-%m-%d")
    datasets = []
    for param in DEFAULT_PARAMS:
        datasets.append(
            f"s3://era5-pds/zarr/{date_to_query.year}/{str.zfill(str(date_to_query.month), 2)}/data/{param}.zarr/"
        )
    stacked_dataset = xarr.open_mfdataset(
        datasets, engine="zarr", storage_options={"anon": True}
    )

    dataset_for_date = stacked_dataset.sel(
        time0=date_to_query.strftime("%Y-%m-%d"),
        time1=date_to_query.strftime("%Y-%m-%d"),
    )

    # We use rio-xarray here to add geospatial data to the xarray
    dataset_for_date = dataset_for_date.rename(lon="x", lat="y")
    dataset_for_date["x"] = dataset_for_date["x"] - 180
    dataset_for_date = dataset_for_date.drop_vars("time1_bounds")
    wgs84_crs = CRS.from_epsg(4326)

    utm_to_wgs84_transformer = pyproj.Transformer.from_crs(
        epsg_code, 4326, always_xy=True
    ).transform

    aoi = {
        "type": "Polygon",
        "coordinates": [
            [
                [topleft[1], topleft[0] - 32000],
                [topleft[1] + 32000, topleft[0] - 32000],
                [topleft[1] + 32000, topleft[0]],
                [topleft[1], topleft[0]],
                [topleft[1], topleft[0] - 32000],
            ]
        ],
    }

    filtered_wgs84 = shapely_tf(utm_to_wgs84_transformer, shape(aoi))
    left84, bottom84, right84, top84 = shape(filtered_wgs84).bounds

    # We do a rough crop in the first instance as rioxarray doesn't need to reproject the whole globe!
    cropped_dataset = dataset_for_date.sel(
        y=slice(top84 + 0.25, bottom84 - 0.25), x=slice(left84 - 0.25, right84 + 0.25)
    )
    dst_transform = affine.Affine(500, 0.0, topleft[1], 0.0, -500, topleft[0])

    cropped_dataset.rio.write_crs(wgs84_crs, inplace=True)
    for i in params:
        cropped_dataset[i] = cropped_dataset[i].astype("float64")
    reprojected_dataset = cropped_dataset.rio.reproject(
        f"EPSG:{epsg_code}",
        transform=dst_transform,
        shape=CHIP_SIZE,
        resampling=Resampling.cubic,
    )
    reprojected_dataset = (
        reprojected_dataset.resample(time0="1D").interpolate("linear").compute()
    )
    reprojected_dataset = (
        reprojected_dataset.resample(time1="1D").interpolate("linear").compute()
    )
    return reprojected_dataset
