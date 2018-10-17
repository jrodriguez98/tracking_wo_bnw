from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import _init_paths

from sacred import Experiment
from model.config import cfg as frcnn_cfg
from model.config import cfg_from_list, cfg_from_file
import os
import os.path as osp
import yaml
import time

from tracker.rfrcnn import FRCNN as rFRCNN
from tracker.vfrcnn import FRCNN as vFRCNN
from tracker.config import cfg, get_output_dir
from tracker.utils import plot_sequence
from tracker.mot_sequence import MOT_Sequence
from tracker.kitti_sequence import KITTI_Sequence
from tracker.tracker import Tracker
from tracker.utils import interpolate
from tracker.resnet import resnet50

import torch
from torch.autograd import Variable
from torch.utils.data import DataLoader
import numpy as np

ex = Experiment()

ex.add_config('experiments/cfgs/tracker.yaml')

# hacky workaround to load the corresponding cnn config and not having to hardcode it here
ex.add_config(ex.configurations[0]._conf['simple_tracker']['cnn_config'])
ex.add_config(ex.configurations[0]._conf['simple_tracker']['frcnn_config'])

Tracker = ex.capture(Tracker, prefix='simple_tracker.tracker')

test = ["MOT17-01", "MOT17-03", "MOT17-06", "MOT17-07", "MOT17-08", "MOT17-12", "MOT17-14"]
train = ["MOT17-13", "MOT17-11", "MOT17-10", "MOT17-09", "MOT17-05", "MOT17-04", "MOT17-02", ]


kitti_train_pedestrian = ["train_%04d_Pedestrian"%(seq) for seq in range(21)]
kitti_test_pedestrian = ["test_%04d_Pedestrian"%(seq) for seq in range(29)]
kitti_train_car = ["train_%04d_Car"%(seq) for seq in range(21)]
kitti_test_car = ["test_%04d_Car"%(seq) for seq in range(29)]
    
@ex.automain
def my_main(simple_tracker, cnn, frcnn, _config):
    # set all seeds
    torch.manual_seed(simple_tracker['seed'])
    torch.cuda.manual_seed(simple_tracker['seed'])
    np.random.seed(simple_tracker['seed'])
    torch.backends.cudnn.deterministic = True

    if frcnn['cfg_file']:
        cfg_from_file(frcnn['cfg_file'])
    if frcnn['set_cfgs']:
        cfg_from_list(frcnn['set_cfgs'])

    print(_config)

    output_dir = osp.join(get_output_dir(simple_tracker['module_name']), simple_tracker['name'])
    
    sacred_config = osp.join(output_dir, 'sacred_config.yaml')
    
    if not osp.exists(output_dir):
        os.makedirs(output_dir)
    with open(sacred_config, 'w') as outfile:
        yaml.dump(_config, outfile, default_flow_style=False)

    seq = []
    if "MOT" in simple_tracker['sequences']:
        if "train" in simple_tracker['sequences']:
            seq = seq + train
        if "test" in simple_tracker['sequences']:
            seq = seq + test
    elif "KITTI" in simple_tracker['sequences']:
        if "Pedestrian" in simple_tracker['sequences']:
            if "train" in simple_tracker['sequences']:
                seq = seq + kitti_train_pedestrian
            if "test" in simple_tracker['sequences']:
                seq = seq + kitti_test_pedestrian
        if "Car" in simple_tracker['sequences']:
            if "train" in simple_tracker['sequences']:
                seq = seq + kitti_train_car
            if "test" in simple_tracker['sequences']:
                seq = seq + kitti_test_car

    ##########################
    # Initialize the modules #
    ##########################
    
    print("[*] Building FRCNN")

    if simple_tracker['network'] == 'vgg16':
        frcnn = vFRCNN()
    elif simple_tracker['network'] == 'res101':
        frcnn = rFRCNN(num_layers=101)
    else:
        raise NotImplementedError("Network not understood: {}".format(simple_tracker['network']))

    frcnn.create_architecture(2, tag='default',
        anchor_scales=frcnn_cfg.ANCHOR_SCALES,
        anchor_ratios=frcnn_cfg.ANCHOR_RATIOS)
    frcnn.eval()
    frcnn.cuda()
    frcnn.load_state_dict(torch.load(simple_tracker['frcnn_weights']))
    
    cnn = resnet50(pretrained=False, **cnn['cnn'])
    cnn.load_state_dict(torch.load(simple_tracker['cnn_weights']))
    cnn.eval()
    cnn.cuda()
    tracker = Tracker(frcnn=frcnn, cnn=cnn)

    print("[*] Beginning evaluation...")

    time_ges = 0

    for s in seq:
        tracker.reset()

        now = time.time()

        print("[*] Evaluating: {}".format(s))

        if "MOT" in simple_tracker['sequences']:
            db = MOT_Sequence(s)
        elif "KITTI" in simple_tracker['sequences']:
            db = KITTI_Sequence(s)
        else:
            raise NotImplementedError("Invalid sequences: {}".format(simple_tracker['sequences']))

        dl = DataLoader(db, batch_size=1, shuffle=False)
        for sample in dl:
            tracker.step(sample)
        results = tracker.get_results()

        #tracker.write_debug(osp.join(output_dir, "debug_{}.txt".format(s)))

        time_ges += time.time() - now

        print("Tracks found: {}".format(len(results)))
        print("[*] Time needed for {} evaluation: {:.3f} s".format(s, time.time() - now))

        if simple_tracker['interpolate']:
            results = interpolate(results)

        db.write_results(results, osp.join(output_dir))
        
        if simple_tracker['write_images']:
            plot_sequence(results, db, osp.join(output_dir, s))
    
    print("[*] Evaluation for all sets (without image generation): {:.3f} s".format(time_ges))
