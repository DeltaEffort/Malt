# Copyright (c) 2020 BlenderNPR and contributors. MIT license.

from Malt.GL.GL import *
from Malt.GL.Texture import Texture
from Malt.GL.RenderTarget import RenderTarget

_shader_src='''
#include "Passes/AO.glsl"
'''

_SHADER = None

class AmbientOcclusion(object):
    
    def __init__(self):
        self.t = None
        self.fbo = None
        self.shader = None
    
    def render(self, pipeline, common_buffer, normal_depth_texture, samples, radius):
        if self.t is None or self.t.resolution != normal_depth_texture.resolution:
            self.t = Texture(normal_depth_texture.resolution, GL_R32F)
            self.fbo = RenderTarget([self.t])

        if self.shader == None:
            global _SHADER
            if _SHADER is None: _SHADER = pipeline.compile_shader_from_source(_shader_src)
            self.shader = _SHADER

        self.shader.textures['IN_NORMAL_DEPTH'] = normal_depth_texture
        self.shader.uniforms['samples'].set_value(samples)
        self.shader.uniforms['radius'].set_value(radius)
        self.shader.bind()
        common_buffer.bind(self.shader.uniform_blocks['COMMON_UNIFORMS'])
        pipeline.draw_screen_pass(self.shader, self.fbo)
        return self.t

        



