import math
import uuid

import pyproj
from osgeo import gdal
from pyproj import CRS
from rasterio.mask import mask
from rasterio.vrt import WarpedVRT
from shapely.geometry import box, shape, mapping
from shapely.ops import transform

from src.constants import CHIP_SIZE


def build_vrt(file_paths):
    """
    For a given group of files, write a temp vrt to disk
    :param file_paths: The paths of the files
    :return: The vrt path
    """
    # We build a vrt for reading multiple COGs as one file - a bit hacky but very convenient
    vrt_path = f"{str(uuid.uuid4())}_temp.vrt"
    vrt = gdal.BuildVRT(vrt_path, file_paths)
    vrt = None
    return vrt_path


def convert_wgs_to_utm(lon, lat):
    """
    Stolen from
    https://stackoverflow.com/questions/40132542/get-a-cartesian-projection-accurate-around-a-lat-lng-pair
    :param lon: Longitude float
    :param lat: Latitude float
    :return: The utm code appropriate for this AOI
    """
    utm_band = str((math.floor((lon + 180) / 6) % 60) + 1)
    if len(utm_band) == 1:
        utm_band = "0" + utm_band
    if lat >= 0:
        epsg_code = "326" + utm_band
        return epsg_code
    epsg_code = "327" + utm_band
    return epsg_code


def buffer_point(point, buffer_m=16000, output_4326=False):
    """
    Given a WGS 84 shapely point, figure out UTM proj for it, reproject point to that utm,
    buffer it and return the bounding box
    :param point: shapely point in EPSG:4326
    :param buffer_m: metres to buffer the point by
    :param output_4326: bool, should the buffered polygon be converted back to EPSG:4326
    :return: shapely polygon and the UTM CRS for it
    """
    epsg_code = int(convert_wgs_to_utm(point.x, point.y))
    utm_crs = CRS.from_epsg(epsg_code)

    # project point 4326 to utm
    wgs84_to_utm_transformer = pyproj.Transformer.from_proj(
        pyproj.Proj(4326),  # source coordinate system
        utm_crs,  # destination coordinate system
        always_xy=True,
    )
    projected_point = transform(wgs84_to_utm_transformer.transform, point)

    # buffer point
    buffered_point = projected_point.buffer(buffer_m).bounds
    bbox = box(
        minx=buffered_point[0],
        miny=buffered_point[1],
        maxx=buffered_point[2],
        maxy=buffered_point[3],
    )

    if output_4326:
        # reproject polygon back to 4326
        utm_to_wgs84_transformer = pyproj.Transformer.from_proj(
            utm_crs, pyproj.Proj(4326)  # source coordinate system
        )  # destination coordinate system
        bbox = transform(utm_to_wgs84_transformer.transform, bbox)

    return bbox, utm_crs


def reproject_coordinates(geojson, inproj_epsg, outproj_epsg):
    """
    Given a geojson polygon in the input crs, reproject the polygon and return in a dictionary usable by rasterio downstream.
    This method iterates over all points within the polygon and reprojects them individually
    :param polygon: polygon to reproject
    :param epsg_code: output projection epsg code
    :return: dictonary for use within rasterio's mask method
    """
    transformer = pyproj.Transformer.from_crs(
        inproj_epsg, outproj_epsg, always_xy=True
    ).transform
    filtered_wgs84 = transform(transformer, shape(geojson))
    geom = mapping(filtered_wgs84)
    return geom


def bounds_to_geojson(bounds):
    """
    Convert a rio BoundingBox to geojson with optional buffer
    :param bounds: BoundingBox
    :return: geojson with applied buffer
    """
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [bounds.left, bounds.top],
                [bounds.right, bounds.top],
                [bounds.right, bounds.bottom],
                [bounds.left, bounds.bottom],
                [bounds.left, bounds.top],
            ]
        ],
    }


def read_geospatial_file(aoi, dst_crs, dst_transform, src):
    """
    Reads a geospatial raster in the desired transform
    :param aoi: aoi to clip to
    :param dst_crs: destination crs
    :param dst_transform: destination transform
    :param src: open rasterio file handler
    :return: the data
    """
    with WarpedVRT(
        src,
        **{
            "height": CHIP_SIZE[0],
            "width": CHIP_SIZE[1],
            "transform": dst_transform,
            "crs": dst_crs,
        },
    ) as vrt:
        data, tf = mask(vrt, [aoi], crop=True)
    return data, tf
