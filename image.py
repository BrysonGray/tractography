#!/usr/bin/env python

'''

Functions for setting up neuron images and ground truth data in swc format for neurite tracking.

Author: Bryson Gray
2024

'''
import torch
import numpy as np
from scipy.linalg import expm
from skimage.draw import line_nd
from skimage.filters import gaussian


class Image:
    """ image class for tracking environment image data

    Parameters
    ----------
    data : ndarray
        array with with channels along the first axis (c x h x w x d)
    dx : list
        the size of each pixel dimension in user defined coordinates.

    """
    def __init__(self, data, dx):
        self.data = data
        self.dx = dx
        if not isinstance(self.data, torch.Tensor):
            self.data = torch.from_numpy(self.data)


    def crop(self, center, radius, pad=True, value=0.0, n_dim=3):
        """ Crop an image around a center point (rounded to the nearest pixel center).
            The cropped image will be smaller than the given radius if it overlaps with the image boundary.

            Parameters
            ----------
            center : list or tuple
                The center of the cropped image in slice-row-col coordinates. This will be rounded to the nearest pixel index.
            radius : int
                The radius of the cropped image. The total width is 2*radius + 1  in each dimension assuming it doesn't intersect with a boundary.
            
            Returns
            -------
            cropped_img : ndarray
                Cropped image
        """
        if n_dim == 3:
            i,j,k = [int(x) for x in center]
            shape = self.data.shape[1:]
            zpad_top = zpad_btm = ypad_front = ypad_back = xpad_left = xpad_right = 0

            if (i + radius) > shape[0]-1:
                zpad_btm = i + radius - (shape[0]-1)
            if (i - radius) < 0:
                zpad_top = radius - i
            if (j + radius) > shape[1]-1: # back is the max y idx
                ypad_back = j + radius - (shape[1]-1) # number of zeros to append in the y dim
            if (j - radius) < 0: # front is zeroth idx
                ypad_front = radius - j
            if (k + radius) > shape[2]-1:
                xpad_right = k + radius - (shape[2]-1) # number of zeros to append in the x dim
            if (k - radius) < 0:
                xpad_left = radius - k
            
            padding = np.array([zpad_top, zpad_btm, ypad_front, ypad_back, xpad_left, xpad_right])
            zrmd_top, zrmd_btm, yrmd_front, yrmd_back, xrmd_left, xrmd_right = np.array([radius]*6) - padding
            
            patch = self.data[:, i-zrmd_top:i+zrmd_btm+1, j-yrmd_front:j+yrmd_back+1, k-xrmd_left:k+xrmd_right+1] # slicing img creates a view (not a copy of img)

            if pad:
                patch_size = 2*radius+1
                patch_ = torch.ones((self.data.shape[0], patch_size, patch_size, patch_size)) * value
                patch_[:, zpad_top:patch_size - zpad_btm, ypad_front:patch_size - ypad_back, xpad_left:patch_size - xpad_right] = patch
                patch = patch_

        elif n_dim == 2:
            i,j = [int(x) for x in center]
            shape = self.data.shape[1:]
            ypad_front = ypad_back = xpad_left = xpad_right = 0

            if (i + radius) > shape[0]-1: # back is the max y idx
                ypad_back = i + radius - (shape[0]-1) # number of zeros to append in the y dim
            if (i - radius) < 0: # front is zeroth idx
                ypad_front = radius - i
            if (j + radius) > shape[1]-1:
                xpad_right = j + radius - (shape[1]-1) # number of zeros to append in the x dim
            if (j - radius) < 0:
                xpad_left = radius - j
            
            padding = np.array([ypad_front, ypad_back, xpad_left, xpad_right])
            yrmd_front, yrmd_back, xrmd_left, xrmd_right = np.array([radius]*4) - padding
            
            patch = self.data[:, i-yrmd_front:i+yrmd_back+1, j-xrmd_left:j+xrmd_right+1] # slicing img creates a view (not a copy of img)

            if pad:
                patch_size = 2*radius+1
                patch_ = torch.ones((self.data.shape[0], patch_size, patch_size)) * value
                patch_[:, ypad_front:patch_size - ypad_back, xpad_left:patch_size - xpad_right] = patch
                patch = patch_

        return patch, padding


    def draw_line_segment(self, segment, width, binary=False, channel=-1, n_dim=3):
        """ Draw a line segment with width.

        Parameters
        ----------
        segment : array_like
            array with two three dimensional points (shape: 2x3)
        
        width : scalar
            segment width
        """
        # get the center of the patch from the segment endpoints
        center = segment.sum(axis=0) / 2
        direction = segment[0] - segment[1]
        segment_length = torch.sqrt(torch.sum(direction**2))

        # unit normalize direction
        direction = direction / segment_length

        # the patch should contain both line end points plus some blur
        L = int(torch.ceil(segment_length/2)) # half the line length, rounded up
        overhang = int(2*width) # include space for 3 standard deviations beyond the line
        patch_radius = L + overhang

        patch_size = 2*patch_radius + 1
        X = torch.zeros([patch_size]*n_dim)
        # get endpoints
        c = torch.Tensor([patch_radius]*n_dim)
        start = torch.round(segment_length*direction + c).to(int)
        end = torch.round(-segment_length*direction + c).to(int)
        line = line_nd(start, end, endpoint=True)
        X[line] = 1.0
        sigma = [d*width/2 for d in self.dx]
        X = torch.Tensor(gaussian(X, sigma=sigma))

        patch, padding = self.crop(center, patch_radius, pad=False, n_dim=n_dim) # patch is a view of self.data (c x h x w x d)
        if n_dim == 2:
            new_patch = X[padding[0]:X.shape[0]-padding[1], padding[2]:X.shape[1]-padding[3]]
            new_patch /= torch.amax(new_patch, dim=(0,1))
        elif n_dim == 3:
            new_patch = X[padding[0]:X.shape[0]-padding[1], padding[2]:X.shape[1]-padding[3], padding[4]:X.shape[2] - padding[5]]
            new_patch /= torch.amax(new_patch, dim=(0,1,2))

        # add segment to patch
        patch[channel] = torch.maximum(new_patch, patch[channel])

        if binary:
            patch[channel] = torch.where(patch[channel] > 0.68, 1.0, 0.0)

        return

        # # compute distance from the vertical segment. To be rotated later
        # x = [np.arange(2*patch_radius+1)*d for d in self.dx]
        # c = np.array([patch_radius*d for d in self.dx])
        # start_point = c - np.array([L, 0, 0]) # bottom of the line
        # end_point = c + np.array([L, 0, 0])
        # X = np.stack(np.meshgrid(*x, indexing='ij'), axis=-1)

def draw_neurite_tree(img, segments):
    """ Draw all segments to reconstruct a whole neurite tree.

    Parameters
    ----------
    img : Three dimensional scalar-valued array

    segments : N x 2 x 4 array. Array of N segments, each consisting of two points, each point defined by a cartesian coordinate and radius (X,Y,Z,R).

    """

    pass

class DataLoader():
    
    def __init__(self, image : str, label : str) -> None:
        pass