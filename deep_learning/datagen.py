# Data generators and augmentation
# Adapted from https://www.kaggle.com/fantineh/data-reader-and-visualization

import re
import numpy as np
from typing import Dict, List, Optional, Text, Tuple
from PIL import Image
import matplotlib.pyplot as plt
from matplotlib import colors
import tensorflow as tf

from config import dataset_config

def random_crop_input_and_output_images(
    input_img: tf.Tensor,
    output_img: tf.Tensor,
    sample_size: int,
    num_in_channels: int,
    num_out_channels: int,
) -> Tuple[tf.Tensor, tf.Tensor]:
    """Randomly axis-align crop input and output image tensors.

    Args:
    input_img: tensor with dimensions HWC.
    output_img: tensor with dimensions HWC.
    sample_size: side length (square) to crop to.
    num_in_channels: number of channels in input_img.
    num_out_channels: number of channels in output_img.
    Returns:
    input_img: tensor with dimensions HWC.
    output_img: tensor with dimensions HWC.
    """
    combined = tf.concat([input_img, output_img], axis=2)
    combined = tf.image.random_crop(
        combined,
        [sample_size, sample_size, num_in_channels + num_out_channels])
    input_img = combined[:, :, 0:num_in_channels]
    output_img = combined[:, :, -num_out_channels:]
    return input_img, output_img

def center_crop_input_and_output_images(
    input_img: tf.Tensor,
    output_img: tf.Tensor,
    sample_size: int,
) -> Tuple[tf.Tensor, tf.Tensor]:
    """Center crops input and output image tensors.

    Args:
    input_img: tensor with dimensions HWC.
    output_img: tensor with dimensions HWC.
    sample_size: side length (square) to crop to.
    Returns:
    input_img: tensor with dimensions HWC.
    output_img: tensor with dimensions HWC.
    """
    central_fraction = sample_size / input_img.shape[0]
    input_img = tf.image.central_crop(input_img, central_fraction)
    output_img = tf.image.central_crop(output_img, central_fraction)
    return input_img, output_img

data_augmentation = tf.keras.Sequential([
  tf.keras.layers.RandomFlip("horizontal_and_vertical"),
    ])

def _clip_and_rescale(inputs: tf.Tensor, key: Text) -> tf.Tensor:
    """Clips and rescales inputs with the stats corresponding to `key`.

    Args:
    inputs: Inputs to clip and rescale.
    key: Key describing the inputs.

    Returns:
    Clipped and rescaled input.

    Raises:
    ValueError if there are no data statistics available for `key`.
    """
    base_key = _get_base_key(key)

    if base_key not in dataset_config["DATA_STATS"]:
        raise ValueError(
            'No data statistics available for the requested key: {}.'.format(key))
    min_val, max_val, _, _ = dataset_config["DATA_STATS"][base_key]
    inputs = tf.clip_by_value(inputs, min_val, max_val)
    if not base_key in dataset_config["FEATURES_NOT_NORM"]:
        inputs = tf.math.divide_no_nan((inputs - min_val), (max_val - min_val))
    elif base_key == "landcover":
        inputs = inputs/100
    return inputs

def _clip_and_normalize(inputs: tf.Tensor, key: Text) -> tf.Tensor:
    """Clips and normalizes inputs with the stats corresponding to `key`.

    Args:
    inputs: Inputs to clip and normalize.
    key: Key describing the inputs.

    Returns:
    Clipped and normalized input.

    Raises:
    ValueError if there are no data statistics available for `key`.
    """
    base_key = _get_base_key(key)
    if base_key not in dataset_config["DATA_STATS"]:
        raise ValueError(
            'No data statistics available for the requested key: {}.'.format(key))
    min_val, max_val, mean, std = dataset_config["DATA_STATS"][base_key]
    inputs = tf.clip_by_value(inputs, min_val, max_val)
    if not base_key in dataset_config["FEATURES_NOT_NORM"]:
        inputs = inputs - mean
        inputs = tf.math.divide_no_nan(inputs, std)
    elif base_key == "landcover":
        inputs = inputs/100
    return inputs

def _get_base_key(key: Text) -> Text:
    """Extracts the base key from the provided key.

    Earth Engine exports TFRecords containing each data variable with its
    corresponding variable name. In the case of time sequences, the name of the
    data variable is of the form 'variable_1', 'variable_2', ..., 'variable_n',
    where 'variable' is the name of the variable, and n the number of elements
    in the time sequence. Extracting the base key ensures that each step of the
    time sequence goes through the same normalization steps.
    The base key obeys the following naming pattern: '([a-zA-Z]+)'
    For instance, for an input key 'variable_1', this function returns 'variable'.
    For an input key 'variable', this function simply returns 'variable'.

    Args:
    key: Input key.

    Returns:
    The corresponding base key.

    Raises:
    ValueError when `key` does not match the expected pattern.
    """
    match = re.match(r'([a-zA-Z1-9_]+)', key)

    if match:
        return match.group(1)
    raise ValueError(
      'The provided key does not match the expected pattern: {}'.format(key))
    
def add_sample_weights(image, label):
  # The weights for each class, with the constraint that:
  #     sum(class_weights) == 1.0
    class_weights = tf.constant(class_weights_input)
    class_weights = class_weights/tf.reduce_sum(class_weights)

  # Create an image of `sample_weights` by using the label at each pixel as an 
  # index into the `class weights` .
    sample_weights = tf.gather(class_weights, indices=tf.cast(label, tf.int32))

    return image, label, sample_weights

def _get_features_dict(
    sample_size: int,
    features: List[Text],
    ) -> Dict[Text, tf.io.FixedLenFeature]:
    """Creates a features dictionary for TensorFlow IO.

    Args:
    sample_size: Size of the input tiles (square).
    features: List of feature names.

    Returns:
    A features dictionary for TensorFlow IO.
    """
    sample_shape = [sample_size, sample_size]
    features = set(features)
    columns = [tf.io.FixedLenFeature(shape=sample_shape, dtype=tf.float32) for _ in features]
    return dict(zip(features, columns))

def replacenan(t):
    return tf.where(tf.math.is_nan(t), tf.zeros_like(t), t)

def _parse_tfr_element(element, features, clip_and_normalize=False,
    clip_and_rescale=True):
    data = {}
    target = {}
    for feat in features:
        if feat == 'tomorrows_fires':
            parse_dic = {
                feat: tf.io.FixedLenFeature([], tf.string),
            }
            example_message = tf.io.parse_single_example(element, parse_dic)
            b_feature = example_message[feat] 
            feature = tf.io.parse_tensor(b_feature, out_type=tf.float32)
            target[feat] = tf.expand_dims(replacenan(feature), axis=-1)

        else:
            parse_dic = {
                feat: tf.io.FixedLenFeature([], tf.string),
            }
            example_message = tf.io.parse_single_example(element, parse_dic)
            b_feature = example_message[feat] 
            feature = tf.io.parse_tensor(b_feature, out_type=tf.float32)
            if not feat in dataset_config["FEATURES_NOT_NORM"]:
                feature = _clip_and_rescale(feature, feat)
            data[feat] = replacenan(feature)
            
    input_features = tf.stack([data[key] for key in list(data.keys())], axis=2)

    output_features = tf.clip_by_value(target["tomorrows_fires"], 0, 1)
    
    return input_features, output_features

def get_dataset(dataset_pattern: Text, data_size: int, sample_size: int,
                batch_size: int, num_in_channels: int, compression_type: Text,
                clip_and_normalize: bool, clip_and_rescale: bool,
                random_crop: bool, center_crop: bool, shuffle: bool) -> tf.data.Dataset:
    """Gets the dataset from the file pattern.

    Args:
    dataset_pattern: Input file pattern.
    data_size: Size of tiles (square) as read from input files.
    sample_size: Size the tiles (square) when input into the model.
    batch_size: Batch size.
    num_in_channels: Number of input channels.
    compression_type: Type of compression used for the input files.
    clip_and_normalize: True if the data should be clipped and normalized, False
      otherwise.
    clip_and_rescale: True if the data should be clipped and rescaled, False
      otherwise.
    random_crop: True if the data should be randomly cropped.
    center_crop: True if the data shoulde be cropped in the center.

    Returns:
    A TensorFlow dataset loaded from the input file pattern, with features
    described in the constants, and with the shapes determined from the input
    parameters to this function.
    """
    if (clip_and_normalize and clip_and_rescale):
        raise ValueError('Cannot have both normalize and rescale.')
    dataset = tf.data.Dataset.list_files(dataset_pattern,shuffle=False,seed=2048)
    dataset = dataset.interleave(
      lambda x: tf.data.TFRecordDataset(x, compression_type=compression_type),
      num_parallel_calls=tf.data.experimental.AUTOTUNE)
    dataset = dataset.prefetch(buffer_size=tf.data.experimental.AUTOTUNE)
    
    dataset = dataset.map(
        lambda x: _parse_tfr_element(x, dataset_config["INPUT_FEATURES"] + dataset_config["OUTPUT_FEATURES"]),
      num_parallel_calls=tf.data.experimental.AUTOTUNE)

    if shuffle:
        dataset = dataset.shuffle(2048)

    dataset = dataset.batch(batch_size)

    dataset = dataset.prefetch(buffer_size=tf.data.experimental.AUTOTUNE)
    
    return dataset