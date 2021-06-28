# Copyright (c) 2020 BlenderNPR and contributors. MIT license. 

import os, time
from itertools import chain
import bpy
from . MaltProperties import MaltPropertyGroup
from . import MaltPipeline


def get_pipeline_graph(context):
    if context is None or context.space_data is None or context.space_data.edit_tree is None:
        return None
    return context.space_data.edit_tree.get_pipeline_graph()

class MaltTree(bpy.types.NodeTree):

    bl_label = "Malt Node Tree"
    bl_icon = 'NODETREE'

    @classmethod
    def poll(cls, context):
        return context.scene.render.engine == 'MALT'
    
    def poll_material(self, material):
        return material.malt.shader_nodes is self
    
    graph_type: bpy.props.StringProperty(name='Type')

    library_source : bpy.props.StringProperty(name="Shader Library", subtype='FILE_PATH')

    disable_updates : bpy.props.BoolProperty(name="Disable Updates", default=False)

    malt_parameters : bpy.props.PointerProperty(type=MaltPropertyGroup)

    def get_source_language(self):
        return self.get_pipeline_graph().language

    def get_transpiler(self):
        if self.get_source_language() == 'GLSL':
            return GLSLTranspiler
        elif self.get_source_language() == 'Python':
            return PythonTranspiler

    def get_library_path(self):
        if self.library_source != '':
            src_path = bpy.path.abspath(self.library_source, library=self.library)
            if os.path.exists(src_path):
                return src_path
        return None
    
    def get_library(self):
        library_path = self.get_library_path()
        if library_path:
            return get_libraries()[library_path]
        else:
            return get_empty_library()

    def get_pipeline_graph(self):
        bridge = MaltPipeline.get_bridge()
        if bridge and self.graph_type in bridge.graphs:
            return bridge.graphs[self.graph_type]
        return None
    
    def get_generated_source_dir(self):
        import os, tempfile
        base_path = '//'
        if bpy.context.blend_data.is_saved == False:
            base_path = tempfile.gettempdir()
        return os.path.join(base_path,'malt-shaders')

    def get_generated_source_path(self):
        import os
        file_prefix = 'temp'
        if bpy.context.blend_data.is_saved:  
            file_prefix = bpy.path.basename(bpy.context.blend_data.filepath).split('.')[0]
        pipeline_graph = self.get_pipeline_graph()
        if pipeline_graph:
            return os.path.join(self.get_generated_source_dir(),'{}-{}{}'.format(file_prefix, self.name, pipeline_graph.file_extension))
        return None
    
    def get_generated_source(self):
        output_nodes = []
        linked_nodes = []
        
        pipeline_graph = self.get_pipeline_graph()
        if pipeline_graph:
            for node in self.nodes:
                if isinstance(node, MaltIONode) and node.is_output:
                    output_nodes.append(node)
                    linked_nodes.append(node)
        
        def add_node_inputs(node, list):
            for input in node.inputs:
                if input.is_linked:
                    new_node = input.links[0].from_node
                    if new_node not in list:
                        add_node_inputs(new_node, list)
                        list.append(new_node)
                    if new_node not in linked_nodes:
                        linked_nodes.append(new_node)
        
        transpiler = self.get_transpiler()
        def get_source(output):
            nodes = []
            add_node_inputs(output, nodes)
            code = ''
            for node in nodes:
                if isinstance(node, MaltNode):
                    code += node.get_source_code(transpiler) + '\n'
            code += output.get_source_code(transpiler)
            return code

        shader ={}
        for output in output_nodes:
            shader[output.io_type] = get_source(output)
        shader['GLOBAL'] = ''
        library_path = self.get_library_path()
        if library_path:
            shader['GLOBAL'] += '#include "{}"\n'.format(library_path)
        for node in linked_nodes:
            if isinstance(node, MaltNode):
                shader['GLOBAL'] += node.get_source_global_parameters(transpiler)
        return pipeline_graph.generate_source(shader)
    
    def reload_nodes(self):
        self.disable_updates = True
        try:
            for node in self.nodes:
                if isinstance(node, MaltNode):
                    node.setup()
            for node in self.nodes:
                if isinstance(node, MaltNode):
                    node.update()
        except:
            import traceback
            traceback.print_exc()
        self.disable_updates = False

    def update(self):
        if self.disable_updates:
            return

        if self.get_pipeline_graph() is None:
            return

        self.disable_updates = True
        try:
            for link in self.links:
                try:
                    if (link.from_socket.data_type != link.to_socket.data_type or 
                        link.from_socket.array_size != link.to_socket.array_size):
                        self.links.remove(link)
                except:
                    pass
            
            source = self.get_generated_source()
            source_dir = bpy.path.abspath(self.get_generated_source_dir())
            source_path = bpy.path.abspath(self.get_generated_source_path())
            import pathlib
            pathlib.Path(source_dir).mkdir(parents=True, exist_ok=True)
            with open(source_path,'w') as f:
                f.write(source)
            from BlenderMalt import MaltMaterial
            MaltMaterial.track_shader_changes()
        except:
            import traceback
            traceback.print_exc()
        self.disable_updates = False
        
        # Force a depsgraph update. 
        # Otherwise these will be outddated in scene_eval
        self.update_tag()

def setup_node_trees():
    graphs = MaltPipeline.get_bridge().graphs
    for name, graph in graphs.items():
        preload_menus(graph.structs, graph.functions)
    
    track_library_changes(force_update=True, disable_tree_updates=True)
    
    for tree in bpy.data.node_groups:
        if tree.bl_idname == 'MaltTree':
            tree.reload_nodes()
            tree.update()

__LIBRARIES = {}    
def get_libraries():
    return __LIBRARIES
def get_empty_library():
    return {
        'structs':{},
        'functions':{},
        'paths':[],
    }
__TIMESTAMP = time.time()

def track_library_changes(force_update=False, disable_tree_updates=False):
    if bpy.context.scene.render.engine != 'MALT' and force_update == False:
        return 1

    global __LIBRARIES
    global __TIMESTAMP
    start_time = time.time()

    #purge unused libraries
    new_dic = {}
    for tree in bpy.data.node_groups:
        if isinstance(tree, MaltTree):
            src_path = tree.get_library_path()
            if src_path:
                if src_path in __LIBRARIES:
                    new_dic[src_path] = __LIBRARIES[src_path]
                else:
                    new_dic[src_path] = None
    __LIBRARIES = new_dic

    needs_update = set()
    for path, library in __LIBRARIES.items():
        root_dir = os.path.dirname(path)
        if os.path.exists(path):
            if library is None:
                needs_update.add(path)
            else:
                for sub_path in library['paths']:
                    sub_path = os.path.join(root_dir, sub_path)
                    if os.path.exists(sub_path):
                        # Don't track individual files granularly since macros can completely change them
                        if os.stat(sub_path).st_mtime > __TIMESTAMP:
                            needs_update.add(path)
                            break
    
    if len(needs_update) > 0:
        results = MaltPipeline.get_bridge().reflect_source_libraries(needs_update)
        for path, reflection in results.items():
            __LIBRARIES[path] = reflection
            preload_menus(reflection['structs'], reflection['functions'])
        
        if disable_tree_updates == False:
            for tree in bpy.data.node_groups:
                if isinstance(tree, MaltTree):
                    src_path = tree.get_library_path()
                    if src_path and src_path in needs_update:
                        tree.update()
    
    __TIMESTAMP = start_time
    return 0.1


__TYPE_COLORS = {}
def get_type_color(type):
    if type not in __TYPE_COLORS:
        import random, hashlib
        seed = hashlib.sha1(type.encode('ascii')).digest()
        rand = random.Random(seed)
        __TYPE_COLORS[type] = (rand.random(),rand.random(),rand.random(),1.0)
    return __TYPE_COLORS[type]

#TODO: Send transpiler along graph types
class SourceTranspiler():
    
    @classmethod
    def asignment(self, name, asignment):
        pass

    @classmethod
    def declaration(self, type, size, name, initialization=None):
        pass

    @classmethod
    def global_reference(self, node_name, parameter_name):
        pass
    
    @classmethod
    def global_declaration(self, type, size, name, initialization=None):
        pass

    @classmethod
    def parameter_reference(self, node_name, parameter_name):
        pass

    @classmethod
    def io_parameter_reference(self, parameter_name):
        return parameter_name

    @classmethod
    def is_instantiable_type(self, type):
        return True

    @classmethod
    def call(self, name, parameters=[], full_statement=False):
        pass

    @classmethod
    def result(self, result):
        pass

    @classmethod
    def scoped(self, code):
        pass

class GLSLTranspiler(SourceTranspiler):

    @classmethod
    def asignment(self, name, asignment):
        return f'{name} = {asignment};\n'

    @classmethod
    def declaration(self, type, size, name, initialization=None):
        array = '' if size == 0 else f'[{size}]'
        asignment = f' = {initialization}' if initialization else ''
        return f'{type} {name}{array}{asignment};\n'

    @classmethod    
    def global_reference(self, node_name, parameter_name):
        return f'U_0{node_name}_0_{parameter_name}'

    @classmethod
    def global_declaration(self, type, size, name, initialization=None):
        return 'uniform ' + self.declaration(type, size, name, initialization)

    @classmethod
    def parameter_reference(self, node_name, parameter_name):
        return f'{node_name}_0_{parameter_name}'

    @classmethod    
    def is_instantiable_type(self, type):
        return type.startswith('sampler') == False

    @classmethod
    def call(self, function, name, parameters=[]):
        src = ''
        for i, parameter in enumerate(function['parameters']):
            if parameter['io'] in ['out','inout']:
                initialization = parameters[i]
                src_reference = self.parameter_reference(name, parameter['name'])
                src += self.declaration(parameter['type'], parameter['size'], src_reference, initialization)
                parameters[i] = src_reference
        
        initialization = f'{function["name"]}({",".join(parameters)})'
        
        if function['type'] != 'void' and self.is_instantiable_type(function['type']):
            src += self.declaration(function['type'], 0, self.parameter_reference(name, 'result'), initialization)
        else:
            src += initialization + ';\n'
        
        return src

    @classmethod
    def result(self, result):
        return f'return {result};\n'

    @classmethod    
    def scoped(self, code):
        import textwrap
        code = textwrap.indent(code, '\t')
        return f'{{\n{code}}}\n'

class PythonTranspiler(SourceTranspiler):

    @classmethod
    def asignment(self, name, asignment):
        return f'{name} = {asignment}\n'

    @classmethod
    def declaration(self, type, size, name, initialization=None):
        if initialization is None: initialization = 'None'
        return self.asignment(name, initialization)

    @classmethod    
    def global_reference(self, node_name, parameter_name):
        return f'PARAMETERS["{node_name}"]["{parameter_name}"]'

    @classmethod    
    def global_declaration(self, type, size, name, initialization=None):
        return ''
        return self.declaration(type, size, name, initialization)

    @classmethod    
    def parameter_reference(self, node_name, parameter_name):
        return f'{node_name}_parameters["{parameter_name}"]'

    @classmethod    
    def io_parameter_reference(self, parameter_name):
        return f'IO["{parameter_name}"]'

    @classmethod
    def call(self, function, name, parameters=[]):
        src = ''
        src += f'{name}_parameters = {{}}\n'
        for i, parameter in enumerate(function['parameters']):
            initialization = parameters[i]
            if initialization is None:
                initialization = 'None'
            parameter_reference = self.parameter_reference(name, parameter['name'])
            src += f'{parameter_reference} = {initialization}\n'
        src += f'run_node("{name}", "{function["name"]}", {name}_parameters)\n'
        return src

    @classmethod
    def result(self, result):
        return f'return {result}\n'

    @classmethod    
    def scoped(self, code):
        import textwrap
        code = textwrap.indent(code, '\t')
        return f'if True:\n{code}'
        

class MaltSocket(bpy.types.NodeSocket):
    
    bl_label = "Malt Node Socket"

    def on_type_update(self, context):
        self.node.on_socket_update(self)

    data_type: bpy.props.StringProperty(update=on_type_update)

    array_size: bpy.props.IntProperty(default=0, update=on_type_update)

    def is_instantiable_type(self):
        return self.data_type.startswith('sampler') == False

    def get_source_reference(self):
        if not self.is_instantiable_type() and not self.is_output and self.get_linked() is not None:
            self.get_linked().get_source_reference()
        else:
            return self.node.get_source_socket_reference(self)
    
    def get_source_global_reference(self):
        return self.id_data.get_transpiler().global_reference(self.node.get_source_name(), self.name)
        return 'U_0{}_0_{}'.format(self.node.get_source_name(), self.name)

    def get_linked(self):
        def get_linked_internal(socket):
            if len(socket.links) == 0:
                return None
            else:
                link = socket.links[0]
                linked = link.to_socket if socket.is_output else link.from_socket
                if isinstance(linked.node, bpy.types.NodeReroute):
                    sockets = linked.node.inputs if linked.is_output else linked.node.outputs
                    if len(sockets) == 0:
                        return None
                    return get_linked_internal(sockets[0])
                else:
                    return linked
        return get_linked_internal(self)
    
    def get_ui_label(self):
        type = self.data_type
        if self.array_size > 0:
            type += '[{}]'.format(self.array_size)
        return '{}   ( {} )'.format(self.name, type)
    
    def draw(self, context, layout, node, text):
        text = self.get_ui_label()
        node.draw_socket(context, layout, self, text)
    
    def setup_shape(self):
        from Malt.Parameter import Parameter
        base_type = True
        try:
            Parameter.from_glsl_type(self.data_type)
        except:
            base_type = False
        array_type = self.array_size > 0
        if base_type:
            if array_type:
                self.display_shape = 'CIRCLE_DOT'
            else:
                self.display_shape = 'CIRCLE'
        else:
            if array_type:
                self.display_shape = 'SQUARE_DOT'
            else:
                self.display_shape = 'SQUARE'

    def draw_color(self, context, node):
        return get_type_color(self.data_type)
    

class MaltNode():

    malt_parameters : bpy.props.PointerProperty(type=MaltPropertyGroup)

    disable_updates : bpy.props.BoolProperty(name="Disable Updates", default=False)

    # Blender will trigger update callbacks even before init and update has finished
    # So we use some wrappers to get a more sane behaviour

    def _disable_updates_wrapper(self, function):
        tree = self.id_data
        tree.disable_updates = True
        self.disable_updates = True
        try:
            function()
        except:
            import traceback
            traceback.print_exc()
        tree.disable_updates = False
        self.disable_updates = False

    def init(self, context):
        self._disable_updates_wrapper(self.malt_init)
        
    def setup(self, context=None):
        self._disable_updates_wrapper(self.malt_setup)

    def update(self):
        if self.disable_updates:
            return
        self._disable_updates_wrapper(self.malt_update)
        
    def malt_init(self):
        pass

    def malt_setup(self):
        pass
    
    def malt_update(self):
        pass

    def on_socket_update(self, socket):
        pass

    def setup_sockets(self, inputs, outputs):
        from Malt.Parameter import Parameter, Type
        def setup(current, new):
            remove = []
            for e in current.keys():
                if e not in new:
                    #TODO: deactivate linked, don't delete them?
                    remove.append(current[e])
            for e in remove:
                current.remove(e)
            for name, (type, size) in new.items():
                if name not in current:
                    current.new('MaltSocket', name)
                if isinstance(type, Parameter):
                    current[name].data_type = type.type_string()
                    current[name].array_size = 0 #TODO
                else:
                    current[name].data_type = type
                    current[name].array_size = size
        setup(self.inputs, inputs)
        setup(self.outputs, outputs)
        parameters = {}
        for name, input in self.inputs.items():
            parameter = None
            if name in inputs.keys() and isinstance(inputs[name][0], Parameter):
                parameter = inputs[name][0]
            elif input.array_size == 0:
                try:
                    parameter = Parameter.from_glsl_type(input.data_type)
                except:
                    pass
            if parameter:
                parameters[input.name] = parameter
        self.malt_parameters.setup(parameters, skip_private=False)
        self.setup_socket_shapes()
        self.setup_width()
    
    def setup_width(self):
        max_len = len(self.name)
        for input in self.inputs.values():
            max_len = max(max_len, len(input.get_ui_label()))
        for output in self.outputs.values():
            max_len = max(max_len, len(output.get_ui_label()))
        #TODO: Measure actual string width
        self.width = max(self.width, max_len * 10)

    def get_source_name(self):
        name = self.name.replace('.','_')
        name = '_' + ''.join(char for char in name if char.isalnum() or char == '_')
        return name.replace('__','_')

    def get_source_code(self, transpiler):
        if self.id_data.get_source_language() == 'GLSL':
            return '/*{} not implemented*/'.format(self)
        elif self.id_data.get_source_language() == 'Python':
            return '# {} not implemented'.format(self)

    def get_source_socket_reference(self, socket):
        if self.id_data.get_source_language() == 'GLSL':
            return '/*{} not implemented*/'.format(socket.name)
        elif self.id_data.get_source_language() == 'Python':
            return '# {} not implemented'.format(socket.name)
    
    def sockets_to_global_parameters(self, sockets, transpiler):
        code = ''
        for socket in sockets:
            if socket.data_type != '' and socket.get_linked() is None:
                code += transpiler.global_declaration(socket.data_type, socket.array_size, socket.get_source_global_reference())
        return code
    
    def get_source_global_parameters(self, transpiler):
        return ''
    
    def setup_socket_shapes(self):
        for socket in chain(self.inputs.values(), self.outputs.values()):
            socket.setup_shape()
    
    def draw_socket(self, context, layout, socket, text):
        layout.label(text=text)
        if socket.is_output == False and socket.is_linked == False:
            self.malt_parameters.draw_parameter(layout, socket.name, None, is_node_socket=True)

    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == 'MaltTree'
    
    def draw_label(self):
        return self.name


class MaltStructNode(bpy.types.Node, MaltNode):
    
    bl_label = "Struct Node"

    def malt_setup(self, context=None):
        struct = self.get_struct()
        self.name = self.struct_type

        inputs = {}
        outputs = {}

        inputs[self.struct_type] = self.struct_type, 0
        outputs[self.struct_type] = self.struct_type, 0

        for member in struct['members']:
            inputs[member['name']] = member['type'], member['size']
            outputs[member['name']] = member['type'], member['size']
        
        self.setup_sockets(inputs, outputs)

    struct_type : bpy.props.StringProperty(update=MaltNode.setup)

    def get_struct(self):
        graph = self.id_data.get_pipeline_graph()
        if self.struct_type in graph.structs:
            return graph.structs[self.struct_type]
        else:
            return self.id_data.get_library()['structs'][self.struct_type]

    def get_source_socket_reference(self, socket):
        if socket.name == self.struct_type:
            return self.get_source_name()
        else:
            return '{}.{}'.format(self.get_source_name(), socket.name)
    
    def struct_input_is_linked(self):
        return self.inputs[self.struct_type].get_linked() is not None

    def get_source_code(self, transpiler):
        code = ''
        node_name = self.get_source_name()
        struct_linked = self.struct_input_is_linked()
        
        initialization = None
        if struct_linked:
            linked = self.inputs[self.struct_type].get_linked()
            initialization = linked.get_source_reference()
        code += transpiler.declaration(self.struct_type, 0, node_name, initialization)
        
        for input in self.inputs:
            if input.data_type != self.struct_type:
                linked = input.get_linked()
                if linked or struct_linked == False:
                    initialization = input.get_source_global_reference()
                    if linked:
                        initialization = linked.get_source_reference()
                    code += transpiler.asignment(input.get_source_reference(), initialization)
        
        return code
    
    def get_source_global_parameters(self, transpiler):
        if self.struct_input_is_linked() == False:
            return self.sockets_to_global_parameters([s for s in self.inputs if s.data_type != self.struct_type], transpiler)
        return ''
    
    def draw_socket(self, context, layout, socket, text):
        if socket.is_output or self.struct_input_is_linked():
            layout.label(text=text)
        else:
            #super() does not work
            MaltNode.draw_socket(self, context, layout, socket, text)
        

class MaltFunctionNode(bpy.types.Node, MaltNode):
    
    bl_label = "Function Node"
    
    def malt_setup(self):
        function = self.get_function()
        self.name = self.function_type

        inputs = {}
        outputs = {}

        if function['type'] != 'void':
            outputs['result'] = function['type'], 0 #TODO: Array return type
        for parameter in function['parameters']:
            if parameter['io'] in ['out','inout']:
                outputs[parameter['name']] = parameter['type'], parameter['size']
            if parameter['io'] in ['','in','inout']:
                inputs[parameter['name']] = parameter['type'], parameter['size']
        
        self.setup_sockets(inputs, outputs)

    function_type : bpy.props.StringProperty(update=MaltNode.setup)

    def get_function(self):
        graph = self.id_data.get_pipeline_graph()
        if self.function_type in graph.functions:
            return graph.functions[self.function_type]
        else:
            return self.id_data.get_library()['functions'][self.function_type]

    def get_source_socket_reference(self, socket):
        transpiler = self.id_data.get_transpiler()
        if transpiler.is_instantiable_type(socket.data_type):
            return transpiler.parameter_reference(self.get_source_name(), socket.name)
        else:
            source = self.get_source_code(transpiler)
            return source.splitlines()[-1].split('=')[-1].split(';')[0]

    def get_source_code(self, transpiler):
        function = self.get_function()
        source_name = self.get_source_name()
        parameters = []
        for parameter in function['parameters']:
            initialization = None
            if parameter['io'] in ['','in','inout']:
                socket = self.inputs[parameter['name']]
                linked = socket.get_linked()
                if linked:
                    initialization = linked.get_source_reference()
                else:
                    initialization = socket.get_source_global_reference()
            parameters.append(initialization)

        return transpiler.call(function, source_name, parameters)
    
    def get_source_global_parameters(self, transpiler):
        return self.sockets_to_global_parameters(self.inputs, transpiler)


class MaltIONode(bpy.types.Node, MaltNode):
    
    bl_label = "IO Node"

    properties: bpy.props.PointerProperty(type=MaltPropertyGroup)
    is_output: bpy.props.BoolProperty()

    def malt_setup(self):
        function = self.get_function()
        self.name = self.io_type + (' Output' if self.is_output else ' Input')

        inputs = {}
        outputs = {}
        
        if function['type'] != 'void' and self.is_output:
            inputs['result'] = function['type'], 0
        for parameter in function['parameters']:
            if parameter['io'] in ['out','inout'] and self.is_output:
                inputs[parameter['name']] = parameter['type'], parameter['size']
            if parameter['io'] in ['','in','inout'] and self.is_output == False:
                outputs[parameter['name']] = parameter['type'], parameter['size']
        
        self.setup_sockets(inputs, outputs)

    io_type : bpy.props.StringProperty(update=MaltNode.setup)

    def get_function(self):
        graph = self.id_data.get_pipeline_graph()
        return graph.graph_IO[self.io_type]

    def get_source_socket_reference(self, socket):
        return self.id_data.get_transpiler().io_parameter_reference(socket.name)
    
    def get_source_code(self, transpiler):
        code = ''
        if self.is_output:
            function = self.get_function()
            for socket in self.inputs:
                if socket.name == 'result':
                    continue
                initialization = socket.get_source_global_reference()
                if socket.get_linked():
                    initialization = socket.get_linked().get_source_reference()
                code += transpiler.asignment(socket.get_source_reference(), initialization)

            if function['type'] != 'void':
                result = socket.get_source_global_reference()
                linked = self.inputs['result'].get_linked()
                if linked:
                    result = linked.get_source_reference()
                code += transpiler.result(result)

        return code
    
    def get_source_global_parameters(self, transpiler):
        return self.sockets_to_global_parameters(self.inputs, transpiler)

class MaltInlineNode(bpy.types.Node, MaltNode):
    
    bl_label = "Inline Code Node"

    def code_update(self, context):
        #update the node tree
        self.id_data.update()

    code : bpy.props.StringProperty(update=code_update)

    def on_socket_update(self, socket):
        self.update()
        self.id_data.update()

    def malt_init(self):
        self.name = 'Inline Code'
        self.malt_update()
    
    def malt_update(self):
        last = 0
        for i, input in enumerate(self.inputs):
            if input.data_type != '' or input.get_linked():
                last = i + 1
        variables = 'abcdefgh'[:min(last+1,8)]
        
        inputs = {}
        for var in variables:
            inputs[var] = '', 0
            if var in self.inputs:
                input = self.inputs[var]
                linked = self.inputs[var].get_linked()
                if linked and linked.data_type != '':
                    inputs[var] = linked.data_type, linked.array_size
                else:
                    inputs[var] = input.data_type, input.array_size
        
        outputs = { 'result' : ('', 0) }
        if 'result' in self.outputs:
            out = self.outputs['result'].get_linked()
            if out:
                outputs['result'] = out.data_type, out.array_size
        
        self.setup_sockets(inputs, outputs)

    def draw_buttons(self, context, layout):
        layout.prop(self, 'code', text='')
    
    def draw_socket(self, context, layout, socket, text):
        if socket.is_output == False:
            layout = layout.split(factor=0.66)
            row = layout.row(align=True).split(factor=0.1)
            row.alignment = 'LEFT'
            MaltNode.draw_socket(self, context, row, socket, socket.name)
            layout.prop(socket, 'data_type', text='')
        else:
            MaltNode.draw_socket(self, context, layout, socket, socket.name)

    def get_source_socket_reference(self, socket):
        return '{}_0_{}'.format(self.get_source_name(), socket.name)
    
    def get_source_code(self, transpiler):
        code = ''
        result_socket = self.outputs['result']
        code += transpiler.declaration(result_socket.data_type, result_socket.array_size, result_socket.get_source_reference())

        scoped_code = ''
        for input in self.inputs:
            if input.data_type != '':
                initialization = input.get_source_global_reference()
                if input.get_linked():
                    initialization = input.get_linked().get_source_reference()
                scoped_code += transpiler.declaration(input.data_type, input.array_size, input.name, initialization)
        if self.code != '':
            scoped_code += transpiler.asignment(self.outputs['result'].get_source_reference(), self.code)

        return code + transpiler.scoped(scoped_code)
    
    def get_source_global_parameters(self, transpiler):
        return self.sockets_to_global_parameters(self.inputs, transpiler)

class MaltArrayIndexNode(bpy.types.Node, MaltNode):
    
    bl_label = "Array Index Node"

    def malt_init(self):
        self.name = 'Array Index'
        self.setup_sockets( { 'array' : ('', 1), 'index' : ('int', 0) },
            { 'element' : ('', 0) } )
        
    def malt_update(self):
        inputs = { 'array' : ('', 1), 'index' : ('int', 0) }
        outputs = { 'element' : ('', 0) }
        
        linked = self.inputs['array'].get_linked()
        if linked and linked.array_size > 0:
            inputs['array'] = linked.data_type, linked.array_size
            outputs['element'] = linked.data_type, 0

        self.setup_sockets(inputs, outputs)

    def get_source_socket_reference(self, socket):
        return '{}_0_{}'.format(self.get_source_name(), socket.name)
    
    def get_source_code(self, transpiler):
        array = self.inputs['array']
        index = self.inputs['index']
        element = self.outputs['element']
        element_reference = index.get_source_global_reference()
        if index.get_linked():
            element_reference = index.get_linked().get_source_reference()
        initialization = '{}[{}]'.format(array.get_linked().get_source_reference(), element_reference)
        return transpiler.declaration(element.data_type, element.array_size, element.get_source_reference(), initialization)
    
    def get_source_global_parameters(self, transpiler):
        return self.sockets_to_global_parameters(self.inputs, transpiler)


class NODE_PT_MaltNodeTree(bpy.types.Panel):

    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Malt Nodes"
    bl_label = "Malt Node Tree UI"

    @classmethod
    def poll(cls, context):
        return context.space_data.tree_type == 'MaltTree'
    
    def draw(self, context):
        layout = self.layout
        #layout.prop(context.space_data.node_tree, 'generated_source')


def preload_menus(structs, functions):
    files = set()
    for name, struct in structs.items():
        files.add(struct['file'])
    for file in files:
        get_structs_menu(file)
    
    files = set()
    for name, function in functions.items():
        files.add(function['file'])
    for file in files:
        get_functions_menu(file)
        

def insert_node(layout, type, label, settings = {}):
    operator = layout.operator("node.add_node", text=label)
    operator.type = type
    operator.use_transform = True
    for name, value in settings.items():
        item = operator.settings.add()
        item.name = name
        item.value = value
    return operator

__FUNCTION_MENUES = {}

def get_functions_menu(file):
    global __FUNCTION_MENUES

    if file not in __FUNCTION_MENUES.keys():
        file_to_label = file.replace('\\', '/').replace('/', ' - ').replace('.glsl', '').replace('_',' ')
        class_name = 'MALT_MT_functions_' + str(len(__FUNCTION_MENUES))
        
        def draw(self, context):
            graph = get_pipeline_graph(context)
            if graph:
                library_functions = context.space_data.node_tree.get_library()['functions']
                for name, function in chain(graph.functions.items(), library_functions.items()):
                    if function['file'] == file:
                        insert_node(self.layout, "MaltFunctionNode", name.replace('_', ' '), settings={
                            'function_type' : repr(name)
                        })

        menu_type = type(class_name, (bpy.types.Menu,), {
            "bl_space_type": 'NODE_EDITOR',
            "bl_label": file_to_label,
            "draw": draw,
        })
        bpy.utils.register_class(menu_type)

        __FUNCTION_MENUES[file] = class_name
    
    return __FUNCTION_MENUES[file]

__STRUCT_MENUES = {}

def get_structs_menu(file):
    global __STRUCT_MENUES

    if file not in __STRUCT_MENUES:
        file_to_label = file.replace('\\', '/').replace('/', ' - ').replace('.glsl', '').replace('_',' ')
        class_name = 'MALT_MT_structs_' + str(len(__STRUCT_MENUES))

        def draw(self, context):
            graph = get_pipeline_graph(context)
            if graph:
                library_structs = context.space_data.node_tree.get_library()['structs']
                for name, struct in chain(graph.structs.items(), library_structs.items()):
                    if struct['file'] == file:
                        insert_node(self.layout, "MaltStructNode", name.replace('_', ' '), settings={
                            'struct_type' : repr(name)
                        })

        menu_type = type(class_name, (bpy.types.Menu,), {
            "bl_space_type": 'NODE_EDITOR',
            "bl_label": file_to_label,
            "draw": draw,
        })
        bpy.utils.register_class(menu_type)

        __STRUCT_MENUES[file] = class_name
    
    return __STRUCT_MENUES[file]


class MALT_MT_NodeFunctions(bpy.types.Menu):
    
    bl_label = "Malt Node Functions Menu"

    def draw(self, context):
        graph = get_pipeline_graph(context)
        if graph:
            files = set()
            library_functions = context.space_data.node_tree.get_library()['functions']
            for name, function in chain(library_functions.items(), graph.functions.items()):
                files.add(function['file'])
            for file in sorted(files):
                self.layout.menu(get_functions_menu(file))

class MALT_MT_NodeStructs(bpy.types.Menu):
    
    bl_label = "Malt Node Structs Menu"

    def draw(self, context):
        graph = get_pipeline_graph(context)
        if graph:
            files = set()
            library_structs = context.space_data.node_tree.get_library()['structs']
            for name, struct in chain(library_structs.items(), graph.structs.items()):
                files.add(struct['file'])
            for file in sorted(files):
                self.layout.menu(get_structs_menu(file))

class MALT_MT_NodeInputs(bpy.types.Menu):
    
    bl_label = "Malt Node Inputs Menu"

    def draw(self, context):
        graph = get_pipeline_graph(context)
        if graph:
            for name in sorted(graph.graph_IO):
                insert_node(self.layout, "MaltIONode", name + ' Input', settings={
                    'is_output' : repr(False),
                    'io_type' : repr(name),
            })

class MALT_MT_NodeOutputs(bpy.types.Menu):
    
    bl_label = "Malt Node Outputs Menu"

    def draw(self, context):
        graph = get_pipeline_graph(context)
        if graph:
            for name in sorted(graph.graph_IO):
                insert_node(self.layout, "MaltIONode", name + ' Ouput', settings={
                    'is_output' : repr(True),
                    'io_type' : repr(name),
            })

class MALT_MT_NodeOther(bpy.types.Menu):
    
    bl_label = "Malt Node Other Menu"

    def draw(self, context):
        graph = get_pipeline_graph(context)
        if graph:
            insert_node(self.layout, "MaltInlineNode", 'Inline Code')
            insert_node(self.layout, "MaltArrayIndexNode", 'Array Index')

def add_node_ui(self, context):
    if context.space_data.tree_type != 'MaltTree':
        return
    if context.space_data.node_tree is None:
        self.layout.label(text='No active node tree')
        return
    if context.space_data.node_tree.graph_type == '':
        self.layout.label(text='No graph type selected')
        return
    graph = get_pipeline_graph(context)
    if graph:
        self.layout.menu("MALT_MT_NodeFunctions", text='Functions')
        self.layout.menu("MALT_MT_NodeStructs", text='Structs')
        self.layout.menu("MALT_MT_NodeInputs", text='Inputs')
        self.layout.menu("MALT_MT_NodeOutputs", text='Outputs')
        self.layout.menu("MALT_MT_NodeOther", text='Other')

def node_header_ui(self, context):
    if context.space_data.tree_type != 'MaltTree' or context.space_data.node_tree is None:
        return
    #self.layout.use_property_split=True
    #self.layout.alignment = 'LEFT'
    self.layout.prop(context.space_data.node_tree, 'library_source',text='')
    self.layout.prop_search(context.space_data.node_tree, 'graph_type', context.scene.world.malt, 'graph_types',text='')
    #self.layout.prop(context.space_data.node_tree, 'edit_material',text='')

    
classes = (
    MaltTree,
    NODE_PT_MaltNodeTree,
    MaltSocket,
    #MaltNode,
    MaltStructNode,
    MaltFunctionNode,
    MaltIONode,
    MaltInlineNode,
    MaltArrayIndexNode,
    MALT_MT_NodeFunctions,
    MALT_MT_NodeStructs,
    MALT_MT_NodeInputs,
    MALT_MT_NodeOutputs,
    MALT_MT_NodeOther,
)

def register():
    for _class in classes: bpy.utils.register_class(_class)

    bpy.types.NODE_MT_add.append(add_node_ui)
    bpy.types.NODE_HT_header.append(node_header_ui)

    bpy.app.timers.register(track_library_changes, persistent=True)
    

def unregister():
    bpy.types.NODE_MT_add.remove(add_node_ui)
    bpy.types.NODE_HT_header.remove(node_header_ui)

    for _class in reversed(classes): bpy.utils.unregister_class(_class)

    bpy.app.timers.unregister(track_library_changes)

