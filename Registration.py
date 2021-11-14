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
from Channel import Channel


#%% Align class
class Registration(Plot):
    '''
    The AlignModel Class is a class used for the optimization of a certain Dataset class. Important is 
    that the loaded class contains the next variables:
        ch1, ch2, ch20 : Nx2 float32
            The two channels (and original channel2) that both contain positions callable by .pos
        linked : bool
            True if the dataset is linked (points are one-to-one)
        img, imgsize, mid: np.array
            The image borders, size and midpoint. Generated by self.imgparams            
    '''
    def __init__(self, execute_linked=True):
        self.execute_linked=execute_linked        
        Plot.__init__(self)
        
        
    #%% Optimization functions
    def Train_Model(self, model, lr=1, epochs=100, opt_fn=tf.optimizers.Adagrad, ch1=None, ch2=None, opt=None):
        if epochs!=0 and epochs is not None:
            if self.BatchOptimization:
                if self.execute_linked: batches=self.counts_linked
                else: batches=self.counts_Neighbours
                if batches is None: raise Exception('Batches have not been initialized yet!')
                self.Nbatches=len(batches)
                print('Training '+model.name+' Mapping with (lr,#it)='+str((lr,epochs))+' for '+str(self.Nbatches)+' Batches...')
            else:
                print('Training '+model.name+' Mapping with (lr,#it)='+str((lr,epochs))+'...')
                batches=None                                             # take whole dataset as single batch
            
            ## Initialize Variables
            if ch1 is None and ch2 is None:
                if self.execute_linked and self.linked:
                    ch1, ch2 = self.ch1, self.ch2
                elif (not self.execute_linked) and self.Neighbours:
                    ch1, ch2 = self.ch1NN, self.ch2NN
                elif self.execute_linked and (not self.linked): raise Exception('Tried to execute linked but dataset has not been linked.')
                elif (not self.execute_linked) and (not self.Neighbours): raise Exception('Tried to execute linked but no neighbours have been generated.')
                else:
                    raise Exception('Dataset is not linked but no Neighbours have been generated yet')
    
            ## The training loop
            if opt is None: opt=opt_fn(lr)
            for i in range(epochs):
                loss=self.train_step(model, epochs, opt, ch1, ch2, batches)  
                if i%100==0 and i!=0: print('iteration='+str(i)+'/'+str(epochs))
            return loss
    
    
    def train_step(self, model, epochs, opt, ch1, ch2, batches=None):
    # the optimization step
        if self.BatchOptimization:  ## work with batches of frames
            pos, i=(0,0)
            for batch in batches: # work with batches 
                idx1=tf.range(pos, pos+batch)[:,None]
                pos1_fr=tf.gather_nd(ch1.pos,idx1)
                pos2_fr=tf.gather_nd(ch2.pos,idx1)
                
                with tf.GradientTape() as tape: # calculate loss
                    if self.execute_linked: loss=self.loss_fn(model,pos1_fr,pos2_fr) 
                    else:  loss=self.loss_fn(model,pos1_fr,pos2_fr, self.Neighbours_mat[i]) 
                
                # calculate and apply gradients
                grads = tape.gradient(loss, model.trainable_weights)
                opt.apply_gradients(zip(grads, model.trainable_weights))
                pos+=batch
                i+=1
                
        else :## take whole dataset as single batch
            with tf.GradientTape() as tape: # calculate loss
                loss=self.loss_fn(model,ch1.pos,ch2.pos) 
            
            # calculate and apply gradients
            grads = tape.gradient(loss, model.trainable_weights)
            opt.apply_gradients(zip(grads, model.trainable_weights))
        
        return loss
    
    
    #@tf.function(experimental_relax_shapes=True)
    def loss_fn(self, model, pos1, pos2, Neighbours_mat=None):
    # The metric that will be optimized
        pos2 = model(pos2)
        if self.execute_linked:
            loss = tf.reduce_sum(tf.square(pos1-pos2))
        else:
            #loss = tf.reduce_sum(tf.abs(pos1-pos2))
            #loss = (tf.reduce_sum(tf.exp(-1*tf.reduce_sum(tf.square(pos1-pos2),axis=-1)/(self.pix_size**2))))
            if Neighbours_mat is None: Neighbours_mat=self.Neighbours_mat
            loss=-tf.reduce_sum(tf.math.log(
                Neighbours_mat @ tf.exp(-1*tf.reduce_sum(tf.square(pos1-pos2),axis=-1)/(self.pix_size**2))[:,None]
                ))
            print('loss='+str(loss))
        return loss
        
            
    def Transform_Model(self, model, ch2=None):
        print('Transforming '+model.name+' Mapping...')
        if model is None: print('Model not trained yet, will pass without transforming.')
        else: 
            if ch2 is None:
                ch2_mapped=model(self.ch2.pos)
                if tf.reduce_any(tf.math.is_nan( ch2_mapped )): 
                    raise ValueError('ch2 contains infinities. The mapping likely exploded.')
                self.ch2.pos.assign(ch2_mapped)
                
                if self.Neighbours: 
                    self.ch2NN.pos.assign(model(self.ch2NN.pos))
                    
            else: 
                ch2_mapped=model(ch2)
                if tf.reduce_any(tf.math.is_nan( ch2_mapped )): 
                    raise ValueError('ch2 contains infinities. The mapping likely exploded.')
                return ch2_mapped
             
        
    #%% CatmullRom Splines
    def InitializeSplines(self, gridsize=3000, edge_grids=1):
        self.ControlPoints=self.generate_CPgrid(gridsize, edge_grids)
        self.edge_grids = edge_grids
        self.gridsize=gridsize
        
        ## Create Nearest Neighbours
        if self.execute_linked and self.linked:
            ## Create variables normalized by gridsize
            ch1_input = Channel(self.InputSplines(self.ch1.pos), self.ch1.frame )
            ch2_input = Channel(self.InputSplines(self.ch2.pos), self.ch2.frame )
        elif (not self.execute_linked) and self.Neighbours:
            ## Create variables normalized by gridsize
            ch1_input = Channel(self.InputSplines(self.ch1NN.pos), self.ch1NN.frame )
            ch2_input = Channel(self.InputSplines(self.ch2NN.pos), self.ch2NN.frame )
        elif self.execute_linked and (not self.linked): raise Exception('Tried to execute linked but dataset has not been linked.')
        elif (not self.execute_linked) and (not self.Neighbours): raise Exception('Tried to execute linked but no neighbours have been generated.')
        else: 
            raise Exception('Trying to calculate loss without Dataset being linked or Neighbours having been generated!')
        
        return ch1_input, ch2_input
    
    
    def InputSplines(self, pts, inverse=False, gridsize=None):
        if inverse:
            return tf.Variable( tf.stack([
                (pts[:,0] + self.x1_min-1 - self.edge_grids) * self.gridsize,
                (pts[:,1] + self.x2_min-1 - self.edge_grids) * self.gridsize 
                ], axis=-1), dtype=tf.float32, trainable=False)
        else:
            return tf.Variable( tf.stack([
                pts[:,0] / self.gridsize - self.x1_min+1 + self.edge_grids,
                pts[:,1] / self.gridsize - self.x2_min+1 + self.edge_grids
                ], axis=-1), dtype=tf.float32, trainable=False)
    
    
    def generate_CPgrid(self, gridsize=3000, edge_grids=1):
            ## Generate the borders of the system
            self.x1_min = np.min([np.min(tf.reduce_min((self.ch1.pos[:,0]))),
                                  np.min(tf.reduce_min((self.ch2.pos[:,0])))])/gridsize
            self.x2_min = np.min([np.min(tf.reduce_min((self.ch1.pos[:,1]))),
                                  np.min(tf.reduce_min((self.ch2.pos[:,1])))])/gridsize
            self.x1_max = np.max([np.max(tf.reduce_max((self.ch1.pos[:,0]))),
                                  np.max(tf.reduce_max((self.ch2.pos[:,0])))])/gridsize
            self.x2_max = np.max([np.max(tf.reduce_max((self.ch1.pos[:,1]))),
                                  np.max(tf.reduce_max((self.ch2.pos[:,1])))])/gridsize   
        
            ## Create grid
            x1_grid = tf.range(0, self.x1_max+2 - self.x1_min+1 + 2*edge_grids, dtype=tf.float32)
            x2_grid = tf.range(0, self.x2_max+2 - self.x2_min+1 + 2*edge_grids, dtype=tf.float32)
            ControlPoints = tf.stack(tf.meshgrid(x1_grid, x2_grid), axis=-1)
            return ControlPoints
            