#!/usr/bin/env python3

# Taken from https://github.com/cornellius-gp/gpytorch/blob/011679a806bc2fe825e3fee8865f82a9e8152c8a/gpytorch/utils/broadcasting.py under the following license:
"""MIT License

Copyright (c) 2017 Jake Gardner

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""

import torch


def _mul_broadcast_shape(*shapes, error_msg=None):
    """Compute dimension suggested by multiple tensor indices (supports broadcasting)"""

    # Pad each shape so they have the same number of dimensions
    num_dims = max(len(shape) for shape in shapes)
    shapes = tuple([1] * (num_dims - len(shape)) + list(shape) for shape in shapes)

    # Make sure that each dimension agrees in size
    final_size = []
    for size_by_dim in zip(*shapes):
        non_singleton_sizes = tuple(size for size in size_by_dim if size != 1)
        if len(non_singleton_sizes):
            if any(size != non_singleton_sizes[0] for size in non_singleton_sizes):
                if error_msg is None:
                    raise RuntimeError("Shapes are not broadcastable for mul operation")
                else:
                    raise RuntimeError(error_msg)
            final_size.append(non_singleton_sizes[0])
        # In this case - all dimensions are singleton sizes
        else:
            final_size.append(1)

    return torch.Size(final_size)


def _matmul_broadcast_shape(shape_a, shape_b, error_msg=None):
    """Compute dimension of matmul operation on shapes (supports broadcasting)"""
    m, n, p = shape_a[-2], shape_a[-1], shape_b[-1]

    if len(shape_b) == 1:
        if n != p:
            if error_msg is None:
                raise RuntimeError(f"Incompatible dimensions for matmul: {shape_a} and {shape_b}")
            else:
                raise RuntimeError(error_msg)
        return shape_a[:-1]

    if n != shape_b[-2]:
        if error_msg is None:
            raise RuntimeError(f"Incompatible dimensions for matmul: {shape_a} and {shape_b}")
        else:
            raise RuntimeError(error_msg)

    tail_shape = torch.Size([m, p])

    # Figure out batch shape
    batch_shape_a = shape_a[:-2]
    batch_shape_b = shape_b[:-2]
    if batch_shape_a == batch_shape_b:
        bc_shape = batch_shape_a
    else:
        bc_shape = _mul_broadcast_shape(batch_shape_a, batch_shape_b)
    return bc_shape + tail_shape


def _pad_with_singletons(obj, num_singletons_before=0, num_singletons_after=0):
    """
    Pad obj with singleton dimensions on the left and right
    Example:
        >>> x = torch.randn(10, 5)
        >>> _pad_width_singletons(x, 2, 3).shape
        >>> # [1, 1, 10, 5, 1, 1, 1]
    """
    new_shape = [1] * num_singletons_before + list(obj.shape) + [1] * num_singletons_after
    return obj.view(*new_shape)