# -*- coding: utf-8 -*-
"""
The align class
"""
import numpy as np
import tensorflow as tf
import copy


from Align_Modules.Affine import AffineModel
from Align_Modules.Polynomial3 import Polynomial3Model
from Align_Modules.RigidBody import RigidBodyModel
from Align_Modules.Splines import CatmullRomSpline2D
from Align_Modules.Shift import ShiftModel

from Plot import Plot


#%% Align class
class Registration(Plot):
    '''
    The AlignModel Class is a class used for the optimization of a certain Dataset class. Important is 
    that the loaded class contains the next variables:
        ch1, ch2, ch2_original : Nx2 float32
            The two channels (and original channel2) that both contain positions callable by .pos
        linked : bool
            True if the dataset is linked (points are one-to-one)
        img, imgsize, mid: np.array
            The image borders, size and midpoint. Generated by self.imgparams            
    '''
    def __init__(self, developer_mode=False):        
        ## Models
        self.AffineModel = None
        self.Polynomial3Model = None
        self.RigidBodyModel = None
        self.ShiftModel = None
        self.SplinesModel = None
        self.CP_locs = None
        self.gridsize=None
        self.edge_grids=None
        self.developer_mode=developer_mode
        
        ## Neighbours
        self.ControlPoints=None
        self.NN_maxDist=None
        self.NN_threshold=None
        self.NN_k=None
        self.Neighbours=False       
        Plot.__init__(self)
                
        
    
    def copy_models(self, other):
        self.AffineModel = copy.deepcopy(other.AffineModel)
        self.Polynomial3Model = copy.deepcopy(other.Polynomial3Model)
        self.RigidBodyModel = copy.deepcopy(other.RigidBodyModel)
        self.ShiftModel = copy.deepcopy(other.ShiftModel)
        self.SplinesModel = copy.deepcopy(other.SplinesModel)
        
        self.CP_locs = copy.deepcopy(other.CP_locs)
        self.gridsize = other.gridsize
        self.edge_grids = other.edge_grids
        if self.gridsize is not None:
            self.x1_min = other.x1_min
            self.x2_min = other.x2_min
            self.x1_max = other.x1_max
            self.x2_max = other.x2_max
        
        
    #%% Optimization functions
    def train_model(self, model, epochs, opt, pos1=None, pos2=None):
        ## initializing the training loop
        if pos1 is None and pos2 is None:
            if self.linked:
                    pos1, pos2 = self.ch1.pos, self.ch2.pos
            elif self.Neighbours:
                    pos1, pos2 = self.ch1NN.pos, self.ch2NN.pos
            else:
                raise Exception('Dataset is not linked but no Neighbours have been generated yet')
           
        ## initializing batches
        if self.FrameOptimization: frame,_=tf.unique(self.ch1.frame) # work with batches of frames
        else: frame=None                                             # take whole dataset as single batch
        
        ## The training loop
        for i in range(epochs):
            loss=self.train_step(model, epochs, opt, pos1, pos2, frame)   
            
        return loss
    
    
    def train_step(self, model, epochs, opt, pos1, pos2, frame=None):
    # the optimization step
        ## take whole dataset as single batch
        if not self.FrameOptimization: 
            with tf.GradientTape() as tape:
                loss=self.loss_fn(model,pos1,pos2) 
                
            grads = tape.gradient(loss, model.trainable_weights)
            opt.apply_gradients(zip(grads, model.trainable_weights))
        ## work with batches of frames
        else:
            for fr in frame: # work with batches 
                idx1=tf.where(self.ch1.frame==fr)
                idx2=tf.where(self.ch2.frame==fr)
            
                pos1_fr=tf.gather_nd(pos1,idx1)
                pos2_fr=tf.gather_nd(pos2,idx2)
                
                with tf.GradientTape() as tape:
                    loss=self.loss_fn(model,pos1_fr,pos2_fr) 
                    
                grads = tape.gradient(loss, model.trainable_weights)
                opt.apply_gradients(zip(grads, model.trainable_weights))
        return loss
    
    
    #@tf.function(experimental_relax_shapes=True)
    def loss_fn(self, model, pos1, pos2):
    # The metric that will be optimized
        pos2 = model(pos1, pos2)
        if self.linked: # for linked dataset, this metric will be the square distance
            loss = tf.reduce_sum(tf.square(pos1-pos2))
        elif self.Neighbours:           # for non-linked datasets, this metric will be the Relative Entropy with a NN algorithm
            CRLB = .15
            D_KL = 0.5*tf.reduce_sum( tf.square(pos1 - pos2) / CRLB**2 , axis=2)
            loss = ( -1*tf.math.log( tf.reduce_sum( tf.math.exp( -1*D_KL / pos2.shape[1] ) / pos2.shape[0] , axis = 1) ) ) 
        else: raise Exception('Trying to calculate loss without Dataset being linked or Neighbours!')
        return loss
    
    
    #%% Global Transforms (Affine, Polynomial3, RigidBody)
    ## Shift
    #@tf.function
    def Train_Shift(self, lr=100, epochs=100):
    # Training the RigidBody Mapping
        if self.ShiftModel is not None: raise Exception('Models can only be trained once')
        
        # initializing the model and optimizer
        self.ShiftModel=ShiftModel(direct=self.linked)
        opt=tf.optimizers.Adagrad(lr)
        
        # Training the Model
        print('Training Shift Mapping (lr, #it) =',str((lr, epochs)),'...')
        _ = self.train_model(self.ShiftModel, epochs, opt)


    def Transform_Shift(self):
    # Transforms ch2 according to the Model
        print('Transforming Shift Mapping...')
        if self.ShiftModel is None: print('Model not trained yet, will pass without transforming.')
        else:
            #for batch in range(len(self.ch1.pos)):
            self.ch2.pos.assign(self.ShiftModel.transform_vec((self.ch2.pos)))
            if tf.reduce_any(tf.math.is_nan( self.ch2.pos )): raise ValueError('ch2 contains infinities. The Shift mapping likely exploded.')    
    
    
    ## RigidBody
    def Train_RigidBody(self, lr=1, epochs=200):
    # Training the RigidBody Mapping
        if self.RigidBodyModel is not None: raise Exception('Models can only be trained once')
        if tf.math.count_nonzero(self.mid)!=0: print('WARNING! The image is not centered. This may have have detrimental effects for mapping a rotation!')
        
        # initializing the model and optimizer
        self.RigidBodyModel=RigidBodyModel(direct=self.linked)
        opt1=tf.optimizers.Adagrad(lr)
        
        # Training the Model
        print('Training RigidBody Mapping (lr, #it) =',str((lr, epochs)),'...')
        _ = self.train_model(self.RigidBodyModel, epochs, opt1)
        
        ## then train the d vector (shift)
        self.RigidBodyModel.d._trainable=True
        self.RigidBodyModel.cos._trainable=False
        opt2=tf.optimizers.Adagrad(lr)
        # Training the Model
        _ = self.train_model(self.RigidBodyModel, epochs, opt2)

    
    def Transform_RigidBody(self):
    # Transforms ch2 according to the Model
        if self.RigidBodyModel is None: print('Model not trained yet, will pass without transforming.')
        else:
            if tf.math.count_nonzero(self.mid)!=0: print('WARNING! The image is not centered. This may have have detrimental effects for mapping a rotation!')
            #for batch in range(len(self.ch1.pos)):
            print('Transforming RigidBody Mapping...')
            self.ch2.pos.assign(self.RigidBodyModel.transform_vec((self.ch2.pos)))
            if tf.reduce_any(tf.math.is_nan( self.ch2.pos )): raise ValueError('ch2 contains infinities. The RigidBody mapping likely exploded.')
        
        
    ## Affine
    def Train_Affine(self, lr=1, epochs=200):
    # Training the Affine Mapping
        if self.AffineModel is not None: raise Exception('Models can only be trained once')
        if tf.math.count_nonzero(self.mid)!=0: print('WARNING! The image is not centered. This may have have detrimental effects for mapping a rotation!')
        
        # initializing the model and optimizer
        self.AffineModel=AffineModel(direct=self.linked)
        
        # Training the Model
        print('Training Affine Mapping with (lr, #it) =',str((lr, epochs)),'...')
        # first train the A matrix (rot, shear, scaling)
        opt1=tf.optimizers.Adagrad(lr)
        ## Training the Model for A
        _ = self.train_model(self.AffineModel, epochs, opt1)
        
        
        ## then train the d vector (shift)
        self.AffineModel.d._trainable=True
        self.AffineModel.A._trainable=False
        opt2=tf.optimizers.Adagrad(lr)
        # Training the Model
        _ = self.train_model(self.AffineModel, epochs, opt2)
        
        
    
    def Transform_Affine(self):
    # Transforms ch2 according to the Model
        if self.AffineModel is None: print('Model not trained yet, will pass without transforming.')
        else:
            if tf.math.count_nonzero(self.mid)!=0: print('WARNING! The image is not centered. This may have have detrimental effects for mapping a rotation!')
            #for batch in range(len(self.ch1.pos)):
            print('Transforming Affine Mapping...')
            self.ch2.pos.assign(self.AffineModel.transform_vec((self.ch2.pos)))
            if tf.reduce_any(tf.math.is_nan( self.ch2.pos )): raise ValueError('ch2 contains infinities. The Affine mapping likely exploded.')
      
        
    ## Polynomial3
    def Train_Polynomial3(self, lr=1, epochs=200):
    # Training the Polynomial3 Mapping
        if self.Polynomial3Model is not None: raise Exception('Models can only be trained once')
        
        # initializing the model and optimizer
        self.Polynomial3Model=Polynomial3Model(direct=self.linked)
        opt=tf.optimizers.Adagrad(lr)
        
        # Training the Model
        _ = self.train_model(self.Polynomial3Model, epochs, opt)
        
    
    def Transform_Polynomial3(self):
    # Transforms ch2 according to the Model
        print('Transforming Polynomial3 Mapping...')
        if self.Polynomial3Model is None: print('Model not trained yet, will pass without transforming.')
        else:
            #for batch in range(len(self.ch1.pos)):
            self.ch2.pos.assign(self.Polynomial3Model.transform_vec((self.ch2.pos)))
            if tf.reduce_any(tf.math.is_nan( self.ch2.pos )): raise ValueError('ch2 contains infinities. The Polynomial3 mapping likely exploded.')
        
        
    #%% CatmullRom Splines
    def Train_Splines(self, lr=1, epochs=200, gridsize=1000, edge_grids=1):
    # Training the Splines Mapping. lr is the learningrate, epochs the number of iterations
    # gridsize the size of the Spline grids and edge_grids the number of gridpoints extra at the edge
        if self.SplinesModel is not None: raise Exception('Models can only be trained once')
        
        ## Generate the borders of the system
        (x1_min, x2_min, x1_max, x2_max) = ([],[],[],[])
        #for batch in range(len(self.ch1.pos)):
        x1_min.append( tf.reduce_min(tf.floor(self.ch2.pos[:,0])) )
        x2_min.append( tf.reduce_min(tf.floor(self.ch2.pos[:,1])) )
        x1_max.append( tf.reduce_max(tf.floor(self.ch2.pos[:,0])) )
        x2_max.append( tf.reduce_max(tf.floor(self.ch2.pos[:,1])) )
        self.x1_min = np.min(x1_min) / gridsize
        self.x2_min = np.min(x2_min) / gridsize
        self.x1_max = np.max(x1_max) / gridsize
        self.x2_max = np.max(x2_max) / gridsize
                                
        ## Create grid
        self.edge_grids = edge_grids
        self.gridsize=gridsize
        x1_grid = tf.range(0, self.x1_max - self.x1_min + self.edge_grids + 2, dtype=tf.float32)
        x2_grid = tf.range(0, self.x2_max - self.x2_min + self.edge_grids + 2, dtype=tf.float32)
        self.ControlPoints = tf.stack(tf.meshgrid(x1_grid, x2_grid), axis=-1)
        
        ## Create Nearest Neighbours       
        if self.linked:
            #for batch in range(len(self.ch1.pos)):
            ## Create variables normalized by gridsize
            ch2_input = tf.Variable( tf.stack([
                self.ch2.pos[:,0] / gridsize - self.x1_min + edge_grids,
                self.ch2.pos[:,1] / gridsize - self.x2_min + edge_grids
                ], axis=-1), dtype=tf.float32, trainable=False) 
            ch1_input = tf.Variable( tf.stack([
                self.ch1.pos[:,0] / gridsize - self.x1_min + edge_grids,
                self.ch1.pos[:,1] / gridsize - self.x2_min + edge_grids
                ], axis=-1), dtype=tf.float32, trainable=False)
        else:
            #for batch in range(len(self.ch1.pos)):
            ## Create variables normalized by gridsize
            ch2_input = tf.Variable( tf.stack([
                self.ch2NN.pos / gridsize - self.x1_min + edge_grids,
                self.ch2NN.pos / gridsize - self.x2_min + edge_grids
                ], axis=-1), dtype=tf.float32, trainable=False)
            ch1_input = tf.Variable( tf.stack([
                self.ch1NN.pos / gridsize - self.x1_min + edge_grids,
                self.ch1NN.pos / gridsize - self.x2_min + edge_grids
                ], axis=-1), dtype=tf.float32, trainable=False)
            
        ## initializing optimizer
        opt=tf.optimizers.Adagrad(lr)
        self.SplinesModel=CatmullRomSpline2D(self.ControlPoints, direct=self.linked)
        
        ## Training the Model
        print('Training Splines Mapping (lr, #it, gridsize) =',str((lr, epochs, gridsize)),'...')
        _ = self.train_model(self.SplinesModel, epochs, opt, ch1_input, ch2_input)
        self.ControlPoints = self.SplinesModel.ControlPoints
                
    
    def Transform_Splines(self):
    # Transforms ch2 according to the Model
        print('Transforming Splines Mapping...')
        if self.SplinesModel is None: print('Model not trained yet, will pass without transforming.')
        else:
            if self.gridsize is None: raise Exception('No Grid has been generated yet')
            
            ch2_input=[]
            #for batch in range(len(self.ch1.pos)):
            ## Create variables normalized by gridsize
            ch2_input = tf.Variable( tf.stack([
                self.ch2.pos[:,0] / self.gridsize - self.x1_min + self.edge_grids,
                self.ch2.pos[:,1] / self.gridsize - self.x2_min + self.edge_grids
                ], axis=-1), dtype=tf.float32, trainable=False) 
                
            # transform the new ch2 model
            #for batch in range(len(self.ch1.pos)):
            ch2_mapped =  self.SplinesModel.transform_vec(ch2_input) 
            self.ch2.pos.assign(tf.stack([
                (ch2_mapped[:,0] + self.x1_min - self.edge_grids) * self.gridsize,
                (ch2_mapped[:,1] + self.x2_min - self.edge_grids) * self.gridsize          
                ], axis=-1))
            if tf.reduce_any(tf.math.is_nan( self.ch2.pos )): raise ValueError('ch2 contains infinities. The Splines mapping likely exploded.')
