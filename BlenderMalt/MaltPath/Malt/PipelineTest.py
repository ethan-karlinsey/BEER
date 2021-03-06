# Copyright (c) 2020 BlenderNPR and contributors. MIT license.

from os import path

from Malt.GL import *
from Malt.Pipeline import Pipeline
from Malt.Mesh import Mesh
from Malt.Shader import Shader
from Malt.Texture import Texture
from Malt.RenderTarget import RenderTarget
from Malt.Parameter import Parameter
from Malt.UBO import UBO

from Malt.Render import Common
from Malt.Render import DepthToCompositeDepth
from Malt.Render import Lighting
from Malt.Render import Line
from Malt.Render import Sampling


_NPR_Pipeline_Common='''
#version 410 core
#extension GL_ARB_shading_language_include : enable

#include "Pipelines/NPR_Pipeline.glsl"
'''

class PipelineTest(Pipeline):

    def __init__(self):
        super().__init__()

        self.sampling_grid_size = 2

        self.parameters.world['Background Color'] = GLUniform(-1, GL_FLOAT_VEC4, (0.5,0.5,0.5,1))
        self.parameters.scene['Line Width Max'] = GLUniform(-1, GL.GL_INT, 10)
        self.parameters.scene['Samples Grid Size Preview'] = GLUniform(-1, GL.GL_INT, 4)
        self.parameters.scene['Samples Grid Size Render'] = GLUniform(-1, GL.GL_INT, 8)
        self.parameters.scene['Samples Width'] = GLUniform(-1, GL.GL_FLOAT, 1.5)
        self.parameters.scene['Shadow Cascades Distribution Exponent'] = GLUniform(-1, GL.GL_INT, 21)
        self.parameters.scene['ShadowMaps Spot Resolution'] = GLUniform(-1, GL.GL_INT, 2048)
        self.parameters.scene['ShadowMaps Sun Resolution'] = GLUniform(-1, GL.GL_INT, 2048)
        self.parameters.scene['ShadowMaps Point Resolution'] = GLUniform(-1, GL.GL_INT, 2048)

        self.common_buffer = Common.CommonBuffer()
        self.lights_buffer = Lighting.LightsBuffer()

        self.line_rendering = Line.LineRendering()

        self.composite_depth = DepthToCompositeDepth.CompositeDepth()

    def compile_material_from_source(self, source, include_paths=[]):
        source = _NPR_Pipeline_Common + source
        return {
            'PRE_PASS' : self.compile_shader_from_source(
                source, 'COMMON_VERTEX_SHADER', 'PRE_PASS_PIXEL_SHADER', include_paths, ['PRE_PASS']
            ),
            'MAIN_PASS' : self.compile_shader_from_source(
                source, 'COMMON_VERTEX_SHADER', 'MAIN_PASS_PIXEL_SHADER', include_paths, ['MAIN_PASS']
            )
        }
    
    def setup_render_targets(self, resolution):
        self.t_depth = Texture(resolution, GL_DEPTH_COMPONENT32F)
        
        self.t_prepass_normal_depth = Texture(resolution, GL_RGBA32F)
        self.t_prepass_id = Texture(resolution, GL_R32F)
        self.fbo_prepass = RenderTarget([self.t_prepass_normal_depth, self.t_prepass_id], self.t_depth)
        
        self.t_main_color = Texture(resolution, GL_RGB32F)
        self.t_line_color = Texture(resolution, GL_RGB32F)
        self.t_line_data = Texture(resolution, GL_RGB32F)
        self.fbo_main = RenderTarget([self.t_main_color, self.t_line_color, self.t_line_data], self.t_depth)

        self.t_color_accumulate = Texture(resolution, GL_RGB32F)
        self.fbo_accumulate = RenderTarget([self.t_color_accumulate])
    
    def get_samples(self, width=1.0):
        return Sampling.get_RGSS_samples(self.sampling_grid_size, width)
    
    def do_render(self, resolution, scene, is_final_render, is_new_frame):
        #SETUP SAMPLING
        if is_final_render:
            self.sampling_grid_size = scene.parameters['Samples Grid Size Render'][0]
        else:
            self.sampling_grid_size = scene.parameters['Samples Grid Size Preview'][0]

        sample_offset = self.get_samples(scene.parameters['Samples Width'][0])[self.sample_count]

        #SETUP PER-OBJECT PARAMETERS
        for i, obj in enumerate(scene.objects):
            obj.parameters['MODEL'] = obj.matrix
            obj.parameters['ID'] = i+1
        
        #SETUP UNIFORM BLOCKS
        self.common_buffer.load(scene, resolution, sample_offset, self.sample_count)
        self.lights_buffer.load(scene, self, 'PRE_PASS', 
            scene.parameters['Shadow Cascades Distribution Exponent'][0],
            scene.parameters['ShadowMaps Spot Resolution'][0],
            scene.parameters['ShadowMaps Sun Resolution'][0],
            scene.parameters['ShadowMaps Point Resolution'][0])

        UBOS = {
            'COMMON_UNIFORMS' : self.common_buffer,
            'SCENE_LIGHTS' : self.lights_buffer
        }

        #PRE-PASS
        self.fbo_prepass.clear([(0,0,1,1), (0,0,0,0)], 1, 0)
        self.draw_scene_pass(self.fbo_prepass, scene.objects, 'PRE_PASS', self.default_shader['PRE_PASS'], UBOS)

        #MAIN-PASS
        textures = {
            'IN_NORMAL_DEPTH': self.t_prepass_normal_depth,
            'IN_ID': self.t_prepass_id,
        }
        callbacks = [
            lambda shader : self.lights_buffer.shader_callback(shader)
        ]
        self.fbo_main.clear([scene.world_parameters['Background Color'], (0,0,0,0), (-1,-1,-1,-1)])
        self.draw_scene_pass(self.fbo_main, scene.objects, 'MAIN_PASS', self.default_shader['MAIN_PASS'], UBOS, {}, textures, callbacks)        
        
        #COMPOSITE LINE
        composited_line = self.line_rendering.composite_line(
            scene.parameters['Line Width Max'], self, self.common_buffer, 
            self.t_main_color, self.t_depth, self.t_prepass_id, self.t_line_color, self.t_line_data)

        # TEMPORAL SUPER-SAMPLING ACCUMULATION
        self.blend_texture(composited_line, self.fbo_accumulate, 1.0 / (self.sample_count + 1))

        #COMPOSITE DEPTH
        composite_depth = None
        if is_final_render:
            composite_depth = self.composite_depth.render(self, self.common_buffer, self.t_depth)

        return {
            'COLOR' : self.t_color_accumulate,
            'DEPTH' : composite_depth,
        }

