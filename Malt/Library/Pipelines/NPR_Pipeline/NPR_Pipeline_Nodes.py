# Copyright (c) 2020-2021 BNPR, Miguel Pozo and contributors. MIT license.

from Malt.Library.Pipelines.NPR_Pipeline.NPR_Pipeline import *

from Malt.PipelineGraph import *

from Malt import PipelineNode

from Malt.PipelineNode import *

from Malt.Library.Nodes import Unpack8bitTextures
from Malt.Library.Pipelines.NPR_Pipeline.Nodes import ScreenPass

from Malt.GL.Texture import internal_format_to_vector_type, internal_format_to_sampler_type, internal_format_to_data_format

_COMMON_HEADER = '''
#include "NPR_Pipeline.glsl"
#include "Node Utils/node_utils.glsl"
'''

_SCREEN_SHADER_HEADER= _COMMON_HEADER + '''
#ifdef PIXEL_SHADER
void SCREEN_SHADER(vec2 uv);
void main(){ SCREEN_SHADER(UV[0]); }
#endif //PIXEL_SHADER
'''

class NPR_Pipeline_Nodes(NPR_Pipeline):

    def __init__(self):
        super().__init__()
        self.parameters.world['Render Layer'] = Parameter('Render Layer', Type.GRAPH)
        self.render_layer_nodes = {}
        self.mesh_shader_custom_output_textures = {}
        self.render_layer_custom_output_accumulate_textures = {}
        self.render_layer_custom_output_accumulate_fbos = {}
        self.draw_layer_counter = 0
        self.setup_graphs()
    
    def get_mesh_shader_custom_outputs(self):
        return {
            'Line Color' : GL_RGBA16F,
            'Line Width' : GL_R16F,
        }
    
    def get_render_layer_custom_outputs(self):
        return {}
    
    def get_mesh_shader_generated_source(self):
        from textwrap import dedent
        header = _COMMON_HEADER + dedent('''
        #ifdef PIXEL_SHADER
        #ifdef MAIN_PASS
        {CUSTOM_OUTPUT_LAYOUT}
        #endif
        #endif

        void CUSTOM_COMMON_PIXEL_SHADER(Surface S, inout PixelOutput PO {CUSTOM_OUTPUT_SIGNATURE});

        void COMMON_PIXEL_SHADER(Surface S, inout PixelOutput PO)
        {{
            {CUSTOM_OUTPUT_DECLARATION}

            CUSTOM_COMMON_PIXEL_SHADER(S, PO {CUSTOM_OUTPUT_CALL});

            #ifdef PIXEL_SHADER
            #ifdef MAIN_PASS
            {{
                {CUSTOM_OUTPUT_ASIGNMENT}
            }}
            #endif
            #endif
        }}
        ''')
        custom_outputs = self.get_mesh_shader_custom_outputs()
        layout = ""
        signature = ""
        declaration = ""
        call = ""
        asignment = ""
        for i, (key, texture_format) in enumerate(custom_outputs.items()):
            key = ''.join(c for c in key if c.isalnum())
            type = internal_format_to_vector_type(texture_format)
            layout += f"layout (location = {i+1}) out {type} OUT_{key};\n"
            signature += f", out {type} {key}"
            declaration += f"{type} {key} = {type}(0);\n"
            call += f", {key}"
            asignment += f"OUT_{key} = {key};\n"

        header = header.format(
            CUSTOM_OUTPUT_LAYOUT = layout,
            CUSTOM_OUTPUT_SIGNATURE = signature,
            CUSTOM_OUTPUT_DECLARATION = declaration,
            CUSTOM_OUTPUT_CALL = call,
            CUSTOM_OUTPUT_ASIGNMENT = asignment,
        )

        reflection_src = f"void CUSTOM_COMMON_PIXEL_SHADER(Surface S, inout PixelOutput PO {signature}) {{}}\n"
        return header, reflection_src
    
    def setup_graphs(self):
        mesh_header, mesh_src = self.get_mesh_shader_generated_source()
        mesh = GLSLPipelineGraph(
            name='Mesh',
            default_global_scope=mesh_header,
            shaders=['PRE_PASS', 'MAIN_PASS', 'SHADOW_PASS'],
            graph_io=[
                GLSLGraphIO(
                    name='CUSTOM_COMMON_PIXEL_SHADER',
                    shader_type='PIXEL_SHADER',
                    custom_output_start_index=2,
                ),
                GLSLGraphIO(
                    name='VERTEX_DISPLACEMENT_SHADER',
                    define='CUSTOM_VERTEX_DISPLACEMENT',
                    shader_type='VERTEX_SHADER'
                ),
                GLSLGraphIO(
                    name='COMMON_VERTEX_SHADER',
                    define='CUSTOM_VERTEX_SHADER',
                    shader_type='VERTEX_SHADER',
                ),
            ]
        )
        mesh.setup_reflection(self, mesh_src)

        light = GLSLPipelineGraph(
            name='Light',
            default_global_scope=_COMMON_HEADER,
            graph_io=[ 
                GLSLGraphIO(
                    name='LIGHT_SHADER',
                    shader_type='PIXEL_SHADER',
                )
            ]
        )
        light.setup_reflection(self, "void LIGHT_SHADER(LightShaderInput I, inout LightShaderOutput O) { }")

        screen = GLSLPipelineGraph(
            name='Screen',
            default_global_scope=_SCREEN_SHADER_HEADER,
            graph_io=[ 
                GLSLGraphIO(
                    name='SCREEN_SHADER',
                    dynamic_input_types= GLSLGraphIO.COMMON_INPUT_TYPES,
                    dynamic_output_types= GLSLGraphIO.COMMON_OUTPUT_TYPES,
                    shader_type='PIXEL_SHADER',
                )
            ]
        )
        screen.setup_reflection(self, "void SCREEN_SHADER(vec2 uv){ }")

        render_layer = PythonPipelineGraph(
            name='Render Layer',
            nodes = [ScreenPass.NODE, Unpack8bitTextures.NODE],
            graph_io = [
                PipelineGraphIO(
                    name = 'Render Layer',
                    function = PipelineNode.static_reflect(
                        name = 'Render Layer',
                        inputs = {
                            'Color' : Parameter('', Type.TEXTURE),
                            'Normal_Depth' : Parameter('', Type.TEXTURE),
                            'ID' : Parameter('', Type.TEXTURE),
                        } + {k : internal_format_to_sampler_type(t) for k,t in self.get_mesh_shader_custom_outputs().items()},
                        outputs = {
                            'Color' : Parameter('', Type.TEXTURE),
                        } + {k : internal_format_to_sampler_type(t) for k,t in self.get_render_layer_custom_outputs().items()},
                    )
                )
            ]
        )
        
        self.graphs = {e.name : e for e in [mesh, light, screen, render_layer]}

    def get_render_outputs(self):
        return super().get_render_outputs() + self.get_render_layer_custom_outputs()

    def setup_render_targets(self, resolution):
        super().setup_render_targets(resolution)

        fbo_main_targets = [self.t_main_color]

        for key, texture_format in self.get_mesh_shader_custom_outputs().items():
            texture = Texture(resolution, texture_format)
            self.mesh_shader_custom_output_textures[key] = texture
            fbo_main_targets.append(texture)

        self.fbo_main = RenderTarget(fbo_main_targets, self.t_depth)

        if self.is_final_render:
            for key, texture_format in self.get_render_layer_custom_outputs().items():
                texture = Texture(resolution, texture_format)
                self.render_layer_custom_output_accumulate_textures[key] = texture
                self.render_layer_custom_output_accumulate_fbos[key] = RenderTarget([texture])

    def do_render(self, resolution, scene, is_final_render, is_new_frame):
        if is_new_frame:
            for fbo in self.render_layer_custom_output_accumulate_fbos.values():
                fbo.clear([(0,0,0,0)])
        self.draw_layer_counter = 0
        result = super().do_render(resolution, scene, is_final_render, is_new_frame)
        result.update(self.render_layer_custom_output_accumulate_textures)
        return result
    
    def draw_layer(self, batches, scene, background_color=(0,0,0,0)):
        clear_colors = [background_color]
        clear_colors.extend([(0)*4] * len(self.mesh_shader_custom_output_textures))
        self.fbo_main.clear(clear_colors)
        
        result = super().draw_layer(batches, scene, background_color)
        
        IN = {
            'Color' : result,
            'Normal_Depth' : self.t_prepass_normal_depth,
            'ID' : self.t_prepass_id,
        }
        IN.update(self.mesh_shader_custom_output_textures)
        OUT = { 'Color' : result }
        
        graph = scene.world_parameters['Render Layer']
        if graph:
            self.graphs['Render Layer'].run_source(graph['source'], graph['parameters'], IN, OUT)

        #TODO: AOV transparency ???
        if self.draw_layer_counter == 0:
            for key, fbo in self.render_layer_custom_output_accumulate_fbos.items():
                if key in OUT and OUT[key]:
                    if internal_format_to_data_format(OUT[key].internal_format) == GL_FLOAT:
                        # TEMPORAL SUPER-SAMPLING ACCUMULATION
                        self.blend_texture(OUT[key], fbo, 1.0 / (self.sample_count + 1))
        #TODO: Pass as parameter
        self.draw_layer_counter += 1
        
        return OUT['Color']

        
PIPELINE = NPR_Pipeline_Nodes

