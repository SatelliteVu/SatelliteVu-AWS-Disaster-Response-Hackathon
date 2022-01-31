# Configuration model training and evaluation.

common_config = {
    "INPUT_DIR": "./input",
    "TEMP_DIR": "./temp",
    "OUTPUT_DIR": "./output"
}

dataset_config = {
    "DATASET_ID": "satvu_data",
    "TRAIN_DATASET_PATTERN": '/satvu_data/*train.tfrecords',
    "EVAL_DATASET_PATTERN": '/satvu_data/*eval.tfrecords',
    "TEST_DATASET_PATTERN": '/satvu_data/*test.tfrecords',
    "INPUT_FEATURES": [
        'elevation',
        'todays_frp',
        'todays_fires'
        ],
    "OUTPUT_FEATURES": ['tomorrows_fires'],
    "FEATURES_NOT_NORM":[
        'PrevFireMask', 
        'FireMask',
        'landcover',
        'todays_fires',
        'tomorrows_fires'
        ],
    "DATA_STATS": {
        'elevation': (563.0764, 1328.4357, 880.3060, 165.8472),
        'todays_frp': (0.0, 83.63587189, 0.606276508, 5.740427949),
        'todays_fires': (0., 1., 0., 1.),
        'tomorrows_fires': (0., 1., 0., 1.)
        },
}

training_config = {
    "NB_EPOCHS": 100,
    "BATCH_SIZE": 64,
    "INITIAL_LEARNING_RATE": 0.0001,
    "OPTIMIZER_NAME": "adam",
    "LOSS_FUNCTION_NAME": "dice_coef_loss"
    }

model_config = {
    "IMG_SIZE": [64, 64],
    "MODEL_NAME": "resunet",
    "TRAIN_FROM_PARENT_MODEL": False,
    "UNFREEZE_ALL_LAYERS": False,
    "NB_LAYERS": 4
    }

test_config = {
    "wandb_model_nickname": "crisp-microwave-181"
    }

classification_config = {
    "SAMPLE_IDS":[2252],
    "wandb_model_nickname": "crisp-microwave-181",
    }