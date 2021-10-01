# -*- coding: utf-8 -*-
"""
Created on Sat Sep 11 12:01:52 2021

@author: Mels
"""
import numpy as np
from photonpy import Dataset
import copy

from AlignModel import AlignModel

class Dataset_hdf5(AlignModel):
    def __init__(self, path, pix_size=1, align_rcc=True, coupled=False):
        '''
        Very simplistic version of something that should look like the Database class

        Parameters
        ----------
        path : List
            List containing one or two path locations.

        Returns
        -------
        None.

        '''        
        self.shift_rcc=None
        self.coupled=coupled
        self.pix_size=pix_size
        
        ## Loading dataset
        if len(path)==1 or isinstance(path,str):
            # Dataset is grouped, meaning it has to be split manually
            print('Loading dataset... \n Grouping...')
            ds = Dataset.load(path[0],saveGroups=True)
            self.ch1 = ds[ds.group==0]
            self.ch2 = ds[ds.group==1]
        elif len(path)==2:
            # Dataset consists over 2 files
            print('Loading dataset...')
            self.ch1 = Dataset.load(path[0])
            self.ch2 = Dataset.load(path[1])
        else:
            raise TypeError('Path invalid')
        
        self.ch1.pos *= self.pix_size
        self.ch2.pos *= self.pix_size
        
        self.ch2_original=copy.deepcopy(self.ch2)                               # making a copy of the original channel
        self.img, self.imgsize, self.mid = self.imgparams()                     # loading the image parameters
        self.center_image()
        if align_rcc: self.align_rcc()                                          # pre-aligning datasets via rcc 
        
        AlignModel.__init__(self)           
            
          
    #%% Loading the dataset functions
    def imgparams(self):
    # calculate borders of system
    # returns a 2x2 matrix containing the edges of the image, a 2-vector containing
    # the size of the image and a 2-vector containing the middle of the image
        img = np.empty([2,2], dtype = float)
        img[0,0] = np.min(( np.min(self.ch1.pos[:,0]), np.min(self.ch2.pos[:,0]) ))
        img[1,0] = np.max(( np.max(self.ch1.pos[:,0]), np.max(self.ch2.pos[:,0]) ))
        img[0,1] = np.min(( np.min(self.ch1.pos[:,1]), np.min(self.ch2.pos[:,1]) ))
        img[1,1] = np.max(( np.max(self.ch1.pos[:,1]), np.max(self.ch2.pos[:,1]) ))
        return img, (img[1,:] - img[0,:]), (img[1,:] + img[0,:])/2
    
    
    def center_image(self):
        if self.mid is None: self.img, self.imgsize, self.mid = self.imgparams() 
        self.ch1.pos -= self.mid
        self.ch2.pos -= self.mid
        self.ch2_original.pos -= self.mid
        self.img, self.imgsize, self.mid = self.imgparams() 

    
    def align_rcc(self):
    # align the dataset using a RCC shift
        print('Alignning both datasets')
        self.shift_rcc = Dataset.align(self.ch1, self.ch2)
        print('\nRCC shift equals', self.shift_rcc)
        if not np.isnan(self.shift_rcc).any():
            self.ch1.pos+= self.shift_rcc
        else: 
            print('Warning: RCC Shift undefined and will be skipped')