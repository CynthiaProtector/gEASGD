# pylint: disable=invalid-name, unused-argument
"""Definition of nn ops"""
from __future__ import absolute_import

import tvm
import topi
from topi.util import get_const_int
from .tensor import _fschedule_broadcast, _fschedule_injective
from . import registry as reg
from .registry import OpPattern

# relu
reg.register_schedule("relu", _fschedule_broadcast)
reg.register_pattern("relu", OpPattern.ELEMWISE)


# leaky_relu
reg.register_schedule("leaky_relu", _fschedule_broadcast)
reg.register_pattern("leaky_relu", OpPattern.ELEMWISE)

# prelu
reg.register_schedule("prelu", _fschedule_broadcast)
reg.register_pattern("prelu", OpPattern.BROADCAST)

# flatten
reg.register_schedule("flatten", _fschedule_broadcast)
reg.register_pattern("flatten", OpPattern.INJECTIVE)


# pad
reg.register_schedule("pad", _fschedule_broadcast)
reg.register_pattern("pad", OpPattern.INJECTIVE)


# layout transform
reg.register_schedule("__layout_transform__", _fschedule_injective)
reg.register_pattern("__layout_transform__", OpPattern.INJECTIVE)


@reg.register_schedule("softmax")
def schedule_softmax(_, outs, target):
    """Schedule definition of softmax"""
    with tvm.target.create(target):
        return topi.generic.schedule_softmax(outs)

reg.register_pattern("softmax", OpPattern.OPAQUE)


# log softmax
@reg.register_schedule("log_softmax")
def schedule_log_softmax(_, outs, target):
    """Schedule definition of softmax"""
    with tvm.target.create(target):
        return topi.generic.schedule_softmax(outs)

# Mark softmax as extern as we do not fuse it in call cases
reg.register_pattern("log_softmax", OpPattern.OPAQUE)


# dense
@reg.register_compute("dense")
def compute_dense(attrs, inputs, _):
    """Compute definition of dense"""
    if attrs.get_bool("use_bias"):
        return topi.nn.dense(inputs[0], inputs[1], bias=inputs[2])
    return topi.nn.dense(inputs[0], inputs[1])

@reg.register_schedule("dense")
def schedule_dense(_, outs, target):
    """Schedule definition of dense"""
    with tvm.target.create(target):
        return topi.generic.schedule_dense(outs)

reg.register_pattern("dense", OpPattern.OUT_ELEMWISE_FUSABLE)


# conv2d
@reg.register_compute("conv2d")
def compute_conv2d(attrs, inputs, _):
    """Compute definition of conv2d"""
    padding = attrs.get_int_tuple("padding")
    strides = attrs.get_int_tuple("strides")
    dilation = attrs.get_int_tuple("dilation")
    groups = attrs.get_int("groups")
    channels = attrs.get_int("channels")
    layout = attrs["layout"]
    assert layout == "NCHW" or layout == "NHWC"
    (dilation_h, dilation_w) = dilation
    if dilation_h < 1 or dilation_w < 1:
        raise ValueError("dilation should be positive value")
    elif dilation == (1, 1):
        kernel = inputs[1]
    elif layout == "NCHW":
        kernel = topi.nn.dilate(inputs[1], [1, 1, dilation_h, dilation_w])
    else: #layout == NHWC
        kernel = topi.nn.dilate(inputs[1], [1, dilation_h, dilation_w, 1])

    if groups == 1:
        out = topi.nn.conv2d(inputs[0], kernel, strides, padding, layout)
    elif groups == get_const_int(inputs[0].shape[1]) and groups == channels:
        out = topi.nn.depthwise_conv2d_nchw(inputs[0], kernel, strides, padding)
    else:
        raise ValueError("not support arbitrary group number for now")
    if attrs.get_bool("use_bias"):
        bias = inputs[2]
        expand_axis = 1 if layout == "NCHW" else 0
        bias = topi.expand_dims(bias, axis=expand_axis, num_newaxis=2)
        out = topi.broadcast_add(out, bias)
    return out

@reg.register_schedule("conv2d")
def schedule_conv2d(attrs, outs, target):
    """Schedule definition of conv2d"""
    groups = attrs.get_int("groups")
    layout = attrs["layout"]
    with tvm.target.create(target):
        if groups == 1 and layout == "NCHW":
            return topi.generic.schedule_conv2d_nchw(outs)
        elif groups == 1 and layout == "NHWC":
            return topi.generic.schedule_conv2d_nhwc(outs)
        return topi.generic.schedule_depthwise_conv2d_nchw(outs)

@reg.register_alter_op_layout("conv2d")
def alter_conv2d_layout(attrs, inputs, tinfos):
    return topi.nn.conv2d_alter_layout(attrs, inputs, tinfos)

reg.register_pattern("conv2d", OpPattern.OUT_ELEMWISE_FUSABLE)

# convolution NCHWc
@reg.register_compute("_contrib_conv2d_NCHWc")
def compute_contrib_conv2d_NCHWc(attrs, inputs, _):
    """Compute definition of conv2d NCHWc"""
    padding = attrs.get_int_tuple("padding")
    strides = attrs.get_int_tuple("strides")
    dilation = attrs.get_int_tuple("dilation")
    kh, kw = attrs.get_int_tuple('kernel_size')
    groups = attrs.get_int("groups")
    channels = attrs.get_int("channels")
    assert dilation == (1, 1), "not support dilate now"
    if groups == 1:
        # pylint: disable=assignment-from-no-return
        out = topi.nn.conv2d_NCHWc(inputs[0], inputs[1], channels, (kh, kw), strides, padding)
        # pylint: enable=assignment-from-no-return
    else:
        raise ValueError("not support arbitrary group number > 1 for now")
    if attrs.get_bool("use_bias"):
        bias = inputs[2]
        bias = topi.expand_dims(bias, axis=1, num_newaxis=2)
        out = topi.broadcast_add(out, bias)
    return out

@reg.register_schedule("_contrib_conv2d_NCHWc")
def schedule_contrib_conv2d_NCHWc(attrs, outs, target):
    """Schedule definition of conv2d NCHWc"""
    groups = attrs.get_int("groups")
    kh, kw = attrs.get_int_tuple('kernel_size')
    oc = attrs.get_int("channels")
    padding = attrs.get_int_tuple("padding")
    strides = attrs.get_int_tuple("strides")
    with tvm.target.create(target):
        if groups == 1:
            return topi.generic.schedule_conv2d_NCHWc(oc, (kh, kw), strides, padding, outs)
        else:
            raise ValueError("not support group number > 1 for now")

reg.register_pattern("_contrib_conv2d_NCHWc", OpPattern.OUT_ELEMWISE_FUSABLE)

# conv2d_transpose
@reg.register_compute("conv2d_transpose")
def compute_conv2d_transpose(attrs, inputs, _):
    """Compute definition of conv2d_transpose"""
    padding = attrs.get_int_tuple("padding")
    strides = attrs.get_int_tuple("strides")
    dilation = attrs.get_int_tuple("dilation")
    groups = attrs.get_int("groups")
    layout = attrs["layout"]
    assert layout == "NCHW", "only support nchw for now"
    assert dilation == (1, 1), "not support dilate now"
    assert groups == 1, "only support groups == 1 for now"
    out = topi.nn.conv2d_transpose_nchw(inputs[0], inputs[1], strides, padding)
    if attrs.get_bool("use_bias"):
        bias = inputs[2]
        bias = topi.expand_dims(bias, axis=1, num_newaxis=2)
        out = topi.broadcast_add(out, bias)
    output_padding = attrs.get_int_tuple("output_padding")
    out = topi.nn.pad(out, \
        [0, 0, 0, 0], [0, 0, output_padding[0], output_padding[1]])
    return out

@reg.register_schedule("conv2d_transpose")
def schedule_conv2d_transpose(attrs, outs, target):
    """Schedule definition of conv2d_transpose"""
    with tvm.target.create(target):
        return topi.generic.schedule_conv2d_transpose_nchw(outs)

reg.register_pattern("conv2d_transpose", OpPattern.OUT_ELEMWISE_FUSABLE)


# max_pool2d
@reg.register_schedule("max_pool2d")
def schedule_max_pool2d(_, outs, target):
    """Schedule definition of max_pool2d"""
    with tvm.target.create(target):
        return topi.generic.schedule_pool(outs)

reg.register_pattern("max_pool2d", OpPattern.OUT_ELEMWISE_FUSABLE)


# avg_pool2d
@reg.register_schedule("avg_pool2d")
def schedule_avg_pool2d(_, outs, target):
    """Schedule definition of avg_pool2d"""
    with tvm.target.create(target):
        return topi.generic.schedule_pool(outs)

reg.register_pattern("avg_pool2d", OpPattern.OUT_ELEMWISE_FUSABLE)


# global_max_pool2d
@reg.register_schedule("global_max_pool2d")
def schedule_global_max_pool2d(_, outs, target):
    """Schedule definition of global_max_pool2d"""
    with tvm.target.create(target):
        return topi.generic.schedule_global_pool(outs)

reg.register_pattern("global_max_pool2d", OpPattern.OUT_ELEMWISE_FUSABLE)


# global_avg_pool2d
@reg.register_schedule("global_avg_pool2d")
def schedule_global_avg_pool2d(_, outs, target):
    """Schedule definition of global_avg_pool2d"""
    with tvm.target.create(target):
        return topi.generic.schedule_global_pool(outs)

reg.register_pattern("global_avg_pool2d", OpPattern.OUT_ELEMWISE_FUSABLE)

# upsampling
@reg.register_schedule("upsampling")
def schedule_upsampling(_, outs, target):
    """Schedule definition of upsampling"""
    with tvm.target.create(target):
        return topi.generic.schedule_injective(outs)

reg.register_pattern("upsampling", OpPattern.INJECTIVE)

@reg.register_compute("lrn")
def compute_lrn(attrs, inputs, _):
    """Compute definition of lrn"""
    size = attrs.get_int("size")
    axis = attrs.get_int("axis")
    alpha = attrs.get_float("alpha")
    beta = attrs.get_float("beta")
    bias = attrs.get_float("bias")
    return topi.nn.lrn(inputs[0], size, axis, alpha, beta, bias)

@reg.register_schedule("lrn")
def schedule_lrn(attrs, outs, target):
    """Schedule definition of lrn"""
    with tvm.target.create(target):
        return topi.generic.schedule_lrn(outs)

reg.register_pattern("lrn", OpPattern.OPAQUE)

@reg.register_compute("l2_normalize")
def compute_l2_normalize(attrs, inputs, _):
    """Compute definition of l2 normalize"""
    eps = attrs.get_float("eps")
    axis = attrs.get_int_tuple("axis")
    return topi.nn.l2_normalize(inputs[0], eps, axis)

@reg.register_schedule("l2_normalize")
def schedule_l2_normalize(attrs, outs, target):
    """Schedule definition of l2 normalize"""
    with tvm.target.create(target):
        return topi.generic.schedule_l2_normalize(outs)

reg.register_pattern("l2_normalize", OpPattern.OUT_ELEMWISE_FUSABLE)
