import os

DEFAULT_PARAMS = [
    "air_pressure_at_mean_sea_level",
    "air_temperature_at_2_metres",
    "air_temperature_at_2_metres_1hour_Maximum",
    "air_temperature_at_2_metres_1hour_Minimum",
    "dew_point_temperature_at_2_metres",
    "eastward_wind_at_100_metres",
    "eastward_wind_at_10_metres",
    "integral_wrt_time_of_surface_direct_downwelling_shortwave_flux_in_air_1hour_Accumulation",
    "lwe_thickness_of_surface_snow_amount",
    "northward_wind_at_100_metres",
    "northward_wind_at_10_metres",
    "precipitation_amount_1hour_Accumulation",
    "sea_surface_temperature",
    "snow_density",
    "surface_air_pressure",
]

CHIP_SIZE = (64, 64)
FIRMS_API_KEY = os.environ.get("FIRMS_API_KEY")
