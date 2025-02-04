import bpy
import bmesh
import mathutils
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty, BoolProperty, EnumProperty
from bpy.types import Operator
from mgs4_mdn_shared import *
import os

bl_info = {
    "name": "Metal Gear Solid 4 MDN Import",
    "author": "cipherxof",
    "version": (1, 0, 0),
    "blender": (4, 3, 2),
    "location": "File > Import > MGS4 MDN (.mdn)",
    "description": "Import MGS4 MDN format",
    "category": "Import-Export",
}

def parse_vertex_component(reader, component_type, base_offset, pos):
    reader.seek(base_offset + pos)
    type_format = (component_type & 0xF0) >> 4
    
    if type_format == MDN_DataType.FLOAT:
        return reader.read_float()
    elif type_format == MDN_DataType.SHORT:
        return reader.read_uint16() / 32767.0
    elif type_format == MDN_DataType.HALFFLOAT:
        return reader.read_half_float()
    elif type_format == MDN_DataType.UBYTE:
        return reader.read_uint8() / 255.0
    elif type_format == MDN_DataType.BYTE: 
        return reader.read_int8() / 127.0
    return 0.0

def find_texture_file(textures_path, strcode):
    for i in range(4, 9):
        fname = ("{:0" + str(i) + "X}").format(strcode)
        
        tex_path = os.path.join(textures_path, fname + ".dds")
        if os.path.exists(tex_path):
            return tex_path

        tex_path = os.path.join(textures_path, fname.lower() + ".dds")
        if os.path.exists(tex_path):
            return tex_path

    print(f"could not find {strcode:08x}.dds")
    return tex_path

def create_armature(bones, name="MDN_Armature"):
    armature = bpy.data.armatures.new(name)
    armature_obj = bpy.data.objects.new(name, armature)
    bpy.context.collection.objects.link(armature_obj)
   
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode='EDIT')
   
    edit_bones = armature.edit_bones
    bone_list = {}
   
    for idx, mdn_bone in enumerate(bones):
        bone_name = f"{mdn_bone.strcode:08X}"
        edit_bone = edit_bones.new(bone_name)
        bone_list[idx] = edit_bone
        edit_bone["bone_table_index"] = idx

        pos = mathutils.Vector(mdn_bone.worldPos[:3])

        edit_bone.head = pos

        if mdn_bone.parent >= 0 and mdn_bone.parent < len(bones):
            parent_pos = mathutils.Vector(bones[mdn_bone.parent].worldPos[:3])
            if (parent_pos - pos).length < 0.001:
                edit_bone.tail = pos + mathutils.Vector((0, 0, 0.1))
            else:
                edit_bone.tail = parent_pos
        else:
            edit_bone.tail = pos + mathutils.Vector((0, 0, 0.1))

    for idx, mdn_bone in enumerate(bones):
        if mdn_bone.parent >= 0 and mdn_bone.parent < len(bones):
            bone_list[idx].parent = bone_list[mdn_bone.parent]
   
    bpy.ops.object.mode_set(mode='OBJECT')
   
    return armature_obj, armature.bones

def setup_bone_weights(mesh_obj, bone_weights, bone_indices, bones, skin_data=None):
    if not (bone_weights and bone_indices):
        return

    skinned_bones = []
    vertex_groups = {}

    if skin_data:
        for i in range(skin_data.count):
            bone_idx = skin_data.boneId[i]
            if bone_idx < len(bones):
                group_name = f"{bones[bone_idx].strcode:08X}"
                vertex_groups[bone_idx] = mesh_obj.vertex_groups.new(name=group_name)
                skinned_bones.append(group_name)

    mesh_obj["skinned_bones"] = skinned_bones

    # Find all actually used bones
    used_bone_indices = set()
    for vert_idx in range(len(mesh_obj.data.vertices)):
        for i in range(4):
            weight_idx = vert_idx * 4 + i
            bone_idx = bone_indices[weight_idx]
            weight = bone_weights[weight_idx]
            if weight > 0 and bone_idx < len(bones):
                used_bone_indices.add(bone_idx)
    
    for bone_idx in used_bone_indices:
        if bone_idx not in vertex_groups and bone_idx < len(bones):
            group_name = f"{bones[bone_idx].strcode:08X}"
            vertex_groups[bone_idx] = mesh_obj.vertex_groups.new(name=group_name)

    # Assign weights to vertices
    for vert_idx in range(len(mesh_obj.data.vertices)):
        weights = []
        for i in range(4):
            weight_idx = vert_idx * 4 + i
            bone_idx = bone_indices[weight_idx]
            weight = bone_weights[weight_idx]
            if bone_idx in vertex_groups and weight > 0:
                weights.append((bone_idx, weight))
        
        if weights:
            total_weight = sum(w for _, w in weights)
            if total_weight > 0:
                for bone_idx, weight in weights:
                    normalized_weight = weight / total_weight
                    vertex_groups[bone_idx].add([vert_idx], normalized_weight, 'REPLACE')
        else:
            first_bone = min(vertex_groups.keys()) if vertex_groups else None
            if first_bone is not None:
                vertex_groups[first_bone].add([vert_idx], 1.0, 'REPLACE')
    
    return vertex_groups
  
def create_material(material, textures, base_path):
    mat_name = f"{material.strcode:08X}"
    b_material = bpy.data.materials.new(name=mat_name)
    b_material.use_nodes = True
    
    flags = {
        'useUV1': (material.flag >> 16) & 1,
        'noSpec': (material.flag >> 17) & 1,
        'hasEnv': (material.flag >> 20) & 1
    }
    
    b_material["mdn_flag"] = material.flag
    b_material["mdn_strcode"] = material.strcode
    b_material["mdn_textureCount"] = material.textureCount
    b_material["mdn_colorCount"] = material.colorCount
    
    b_material["mdn_diffuseIndex"] = material.diffuseIndex
    b_material["mdn_normalIndex"] = material.normalIndex
    b_material["mdn_specularIndex"] = material.specularIndex
    b_material["mdn_filterIndex"] = material.filterIndex
    b_material["mdn_ambientIndex"] = material.ambientIndex
    b_material["mdn_specGradientIndex"] = material.specGradientIndex
    b_material["mdn_wrinkleIndex"] = material.wrinkleIndex
    b_material["mdn_unknownIndex"] = material.unknownIndex
    
    b_material["mdn_diffuse_color"] = material.diffuse_color
    b_material["mdn_specular_color"] = material.specular_color
    b_material["mdn_unknown_color1"] = material.unknown_color1
    b_material["mdn_unknown_color2"] = material.unknown_color2
    b_material["mdn_unknown_color3"] = material.unknown_color3
    b_material["mdn_unknown_color4"] = material.unknown_color4
    b_material["mdn_unknown_color5"] = material.unknown_color5
    b_material["mdn_unknown_color6"] = material.unknown_color6

    nodes = b_material.node_tree.nodes
    links = b_material.node_tree.links
    nodes.clear()
    
    output = nodes.new('ShaderNodeOutputMaterial')
    principled = nodes.new('ShaderNodeBsdfPrincipled')
    
    if flags['noSpec']:
        principled.inputs['Specular IOR Level'].default_value = 0.0
        principled.inputs['Metallic'].default_value = 0.0
        principled.inputs['Roughness'].default_value = 1.0
    else:
        principled.inputs['Specular IOR Level'].default_value = material.specular_color[0]
        principled.inputs['Metallic'].default_value = material.specular_color[1]
        principled.inputs['Roughness'].default_value = material.specular_color[2]
        
    principled.inputs['Base Color'].default_value = material.diffuse_color
    principled.inputs['Alpha'].default_value = material.diffuse_color[3]
    
    links.new(principled.outputs[0], output.inputs[0])
    
    output.location = (300, 300)
    principled.location = (0, 300)
    
    texture_slots = [
        (material.diffuseIndex, "Diffuse", 0, "Base Color"),
        (material.normalIndex, "Normal", 1, "Normal"),
        (material.specularIndex, "Specular", 2, "Specular IOR Level")
    ]
    
    for tex_idx, tex_type, y_offset, input_name in texture_slots:
        if tex_idx >= 0 and tex_idx < len(textures):
            texture = textures[tex_idx]
            tex_path = find_texture_file(base_path, texture.strcode)
            
            if tex_path and os.path.exists(tex_path):
                img_name = f"{texture.strcode:08X}"
                if img_name in bpy.data.images:
                    img = bpy.data.images[img_name]
                else:
                    img = bpy.data.images.load(tex_path)
                    img.name = img_name
                
                tex_node = nodes.new('ShaderNodeTexImage')
                tex_node.image = img
                tex_node.location = (-300, 300 - y_offset * 300)
                
                tex_node["mdn_strcode"] = texture.strcode
                tex_node["mdn_flag"] = texture.flag
                tex_node["mdn_scaleU"] = texture.scaleU
                tex_node["mdn_scaleV"] = texture.scaleV
                tex_node["mdn_posU"] = texture.posU
                tex_node["mdn_posV"] = texture.posV
                tex_node["mdn_texture_type"] = tex_type
                
                mapping = nodes.new('ShaderNodeMapping')
                mapping.inputs['Scale'].default_value[0] = texture.scaleU
                mapping.inputs['Scale'].default_value[1] = texture.scaleV
                mapping.inputs['Location'].default_value[0] = texture.posU
                mapping.inputs['Location'].default_value[1] = texture.posV
                mapping.location = (-500, 300 - y_offset * 300)
                
                uv_map = nodes.new('ShaderNodeUVMap')
                uv_map.uv_map = f"UV{y_offset}"
                uv_map.location = (-700, 300 - y_offset * 300)
                
                links.new(uv_map.outputs[0], mapping.inputs[0])
                links.new(mapping.outputs[0], tex_node.inputs[0])
                
                if tex_type == "Diffuse":
                    links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
                    if tex_node.image.channels == 4:
                        links.new(tex_node.outputs['Alpha'], principled.inputs['Alpha'])
                        b_material.blend_method = 'HASHED'
                elif tex_type == "Normal" and not flags['noSpec']:
                    normal_map = nodes.new('ShaderNodeNormalMap')
                    normal_map.location = (-100, 0)
                    links.new(tex_node.outputs['Color'], normal_map.inputs['Color'])
                    links.new(normal_map.outputs['Normal'], principled.inputs['Normal'])
                    normal_map.inputs['Strength'].default_value = 0.7
                elif tex_type == "Specular" and not flags['noSpec']:
                    links.new(tex_node.outputs['Color'], principled.inputs['Specular IOR Level'])
    
    return b_material

def read_vertex_buffer(reader, vertex_def, num_vertices):
    vertices = []
    normals = []
    tangents = []
    uvs = {f'UV{i}': [] for i in range(8)}
    bone_weights = []
    bone_indices = []
    vertex_colors = []
    base_offset = reader.offset

    for v in range(num_vertices):
        vert_start = base_offset + (v * vertex_def.stride)
        vertex_data = {'pos': None, 'normal': None, 'tangent': None, 'uv': None, 'weights': None, 'bones': None, 'color': None}
        
        for def_idx in range(vertex_def.defintionCount):
            def_type = vertex_def.definition[def_idx]
            if def_type == 0:
                continue
                
            component_type = def_type & 0x0F
            type_format = (def_type & 0xF0) >> 4
            pos = vertex_def.position[def_idx]
            
            try:
                if component_type == MDN_Definition.POSITION:
                    x = parse_vertex_component(reader, def_type, vert_start, pos)
                    y = parse_vertex_component(reader, def_type, vert_start, pos + 4)
                    z = parse_vertex_component(reader, def_type, vert_start, pos + 8)
                    vertex_data['pos'] = (x, y, z)

                elif component_type == MDN_Definition.NORMAL or component_type == MDN_Definition.TANGENT:
                    reader.seek(vert_start + pos)
                    if type_format == MDN_DataType.FLOAT_COMPRESSED:
                        packed = reader.read_uint32()

                        nx = ((packed) & 0x7FF)
                        ny = ((packed >> 11) & 0x7FF)
                        nz = ((packed >> 22) & 0x3FF)
                        
                        x = nx / 1023.0 if nx <= 1023 else (nx - 2048) / 1023.0
                        y = ny / 1023.0 if ny <= 1023 else (ny - 2048) / 1023.0
                        z = nz / 511.0 if nz <= 511 else (nz - 1024) / 511.0
                        
                        length = math.sqrt(x*x + y*y + z*z)
                        if length > 0:
                            x /= length
                            y /= length
                            z /= length

                        if component_type == MDN_Definition.NORMAL:
                            vertex_data['normal'] = (x, y, z)
                        else:
                            vertex_data['tangent'] = (x, y, z)

                elif component_type == MDN_Definition.COLOUR:
                    reader.seek(vert_start + pos)
                    r = reader.read_uint8() / 255.0
                    g = reader.read_uint8() / 255.0
                    b = reader.read_uint8() / 255.0
                    a = reader.read_uint8() / 255.0
                    vertex_data['color'] = (r, g, b, a)
                    
                elif component_type in [MDN_Definition.TEXTURE3DS, 
                                     MDN_Definition.TEXTURE00,
                                     MDN_Definition.TEXTURE01,
                                     MDN_Definition.TEXTURE02,
                                     MDN_Definition.TEXTURE03,
                                     MDN_Definition.TEXTURE04,
                                     MDN_Definition.TEXTURE05]:
                    u = parse_vertex_component(reader, def_type, vert_start, pos)
                    v = -parse_vertex_component(reader, def_type, vert_start, pos + 2)
                    
                    uv_index = 0
                    if component_type == MDN_Definition.TEXTURE3DS:
                        uv_index = 0
                    else:
                        uv_index = component_type - MDN_Definition.TEXTURE00
                    
                    uvs[f'UV{uv_index}'].append((u, v))
                
                elif component_type == MDN_Definition.WEIGHT:
                    weights = [
                        max(0.0, min(1.0, parse_vertex_component(reader, def_type, vert_start, pos))),
                        max(0.0, min(1.0, parse_vertex_component(reader, def_type, vert_start, pos + 1))),
                        max(0.0, min(1.0, parse_vertex_component(reader, def_type, vert_start, pos + 2))),
                        max(0.0, min(1.0, parse_vertex_component(reader, def_type, vert_start, pos + 3)))
                    ]
                    weight_sum = sum(weights)
                    if weight_sum > 0:
                        weights = [w / weight_sum for w in weights]

                    #print(f"[{v}] = {weights}")

                    vertex_data['weights'] = weights
                    
                elif component_type == MDN_Definition.BONEIDX:
                    reader.seek(vert_start + pos)
                    bones = [
                        reader.read_uint8(),
                        reader.read_uint8(),
                        reader.read_uint8(),
                        reader.read_uint8()
                    ]
                    #print(f"[{v}] = {bones}")
                    vertex_data['bones'] = bones
                    
            except Exception as e:
                print(f"Error reading component type 0x{def_type:X} at position 0x{reader.offset:X}")
                print(f"Error details: {e}")
                continue
        
        if vertex_data['pos']:
            vertices.append(vertex_data['pos'])
        if vertex_data['normal']:
            normals.append(vertex_data['normal'])
        if vertex_data['tangent']:
            tangents.append(vertex_data['tangent'])
        if vertex_data['weights']:
            bone_weights.extend(vertex_data['weights'])
        if vertex_data['bones']:
            bone_indices.extend(vertex_data['bones'])
        if vertex_data['color']:
            vertex_colors.append(vertex_data['color'])

    reader.seek(base_offset + (num_vertices * vertex_def.stride))
    return vertices, normals, tangents, uvs, bone_weights, bone_indices, vertex_colors

def apply_mesh_data(mesh_obj, vertices, normals, tangents, face_indices, use_smooth=True):
    mesh = mesh_obj.data
    mesh.from_pydata(vertices, [], face_indices)
    mesh.update()
    
    if use_smooth:
        for poly in mesh.polygons:
            poly.use_smooth = True
    
    if normals and len(normals) == len(vertices):
        #mesh.use_auto_smooth = True
        #mesh.auto_smooth_angle = math.radians(180)
        
        split_normals = []
        for poly in mesh.polygons:
            for loop_idx, vertex_idx in zip(poly.loop_indices, poly.vertices):
                if vertex_idx < len(normals):
                    split_normals.append(normals[vertex_idx])
        
        mesh.normals_split_custom_set(split_normals)
    
    if tangents and len(tangents) == len(vertices):
        tangent_data = []
        for t in tangents:
            tangent_data.extend([t[0], t[1], t[2]])

    mesh.validate()
    mesh.update()

def read_face_buffer(reader, face):
    indices = []
    num_triangles = face.count // 3
    
    for i in range(num_triangles):
        v1 = reader.read_uint16()
        v2 = reader.read_uint16()
        v3 = reader.read_uint16()
        indices.append((v1, v2, v3))
    
    return indices

class ImportMDN(Operator, ImportHelper):
    bl_idname = "import_mesh.mdn"
    bl_label = "Import MDN"
    filename_ext = ".mdn"

    filter_glob: StringProperty(
        default="*.mdn",
        options={'HIDDEN'},
    )

    def read_mdn_data(self, context, filepath):
        print(f"\nImporting MDN: {filepath}")
        base_path = os.path.dirname(filepath)
        
        with open(filepath, 'rb') as file:
            data = file.read()
        
        reader = BinaryReader(data, little_endian=False)
        header = MDN_Header.read(reader)
        
        # Read all data structures first
        reader.seek(header.boneOffset)
        bones = [MDN_Bone.read(reader) for _ in range(header.numBones)]
        
        reader.seek(header.skinOffset)
        skins = [MDN_Skin.read(reader) for _ in range(header.numSkin)] if header.numSkin > 0 else []
        
        reader.seek(header.materialOffset)
        materials = [MDN_Material.read(reader) for _ in range(header.numMaterial)]
        
        reader.seek(header.textureOffset)
        textures = [MDN_Texture.read(reader) for _ in range(header.numTexture)]
        
        reader.seek(header.groupOffset)
        groups = [MDN_Group.read(reader) for _ in range(header.numGroups)]
        
        reader.seek(header.meshOffset)
        meshes = [MDN_Mesh.read(reader) for _ in range(header.numMesh)]
        
        reader.seek(header.vertexDefinitionOffset)
        vertex_defs = [MDN_VertexDefinition.read(reader) for _ in range(header.numVertexDefinition)]
        
        reader.seek(header.faceOffset)
        faces = [MDN_Face.read(reader) for _ in range(header.numFace)]
        
        # Create armature first if we have bones
        armature_obj = None
        if bones:
            armature_obj, bone_data = create_armature(bones)
            
        # Create materials
        b_materials = {}
        for material in materials:
            b_materials[material.strcode] = create_material(material, textures, base_path + "/textures")
        
        # Create parent groups first
        group_objects = {}
        for idx, group in enumerate(groups):
            group_name = f"MeshGroup_{group.strcode:08X}"
            empty_obj = bpy.data.objects.new(group_name, None)
            empty_obj.empty_display_type = 'CUBE'
            bpy.context.collection.objects.link(empty_obj)
            group_objects[idx] = empty_obj
        
        # Set up group hierarchy
        for idx, group in enumerate(groups):
            if group.parent >= 0 and group.parent < len(groups):
                group_objects[idx].parent = group_objects[group.parent]
        
        # Create meshes and assign to groups
        mesh_objects = {}
        for mesh_idx, mdn_mesh in enumerate(meshes):
            # Verify indices
            if mdn_mesh.vertexDefIdx >= len(vertex_defs) or mdn_mesh.faceIdx >= len(faces):
                print(f"Invalid indices for mesh {mesh_idx}")
                continue
                
            mesh_name = f"MDN_Mesh_{mesh_idx}"
            mesh_data = bpy.data.meshes.new(name=mesh_name)
            mesh_obj = bpy.data.objects.new(mesh_name, mesh_data)
            bpy.context.collection.objects.link(mesh_obj)
            
            # Read vertex data
            vertex_def = vertex_defs[mdn_mesh.vertexDefIdx]
            reader.seek(header.vertexBufferOffset + vertex_def.offset)
            vertices, normals, tangents, uvs, bone_weights, bone_indices, vertex_colors = read_vertex_buffer(
                reader, vertex_def, mdn_mesh.numVertex)
            
            # Read faces and assign materials
            face_indices = []
            face_materials = []
            if mdn_mesh.numFaceIdx > 0:
                face_start_idx = 0
                for face_idx in range(mdn_mesh.faceIdx, mdn_mesh.faceIdx + mdn_mesh.numFaceIdx):
                    face = faces[face_idx]
                    reader.seek(header.faceBufferOffset + face.offset)
                    section_indices = read_face_buffer(reader, face)
                    face_indices.extend(section_indices)
                    
                    if face.matGroup < len(materials):
                        material = materials[face.matGroup]
                        if material.strcode in b_materials:
                            if face.matGroup not in [mat["mdn_strcode"] for mat in mesh_data.materials]:
                                mesh_data.materials.append(b_materials[material.strcode])
                            mat_idx = len(mesh_data.materials) - 1
                            face_materials.extend([mat_idx] * len(section_indices))
                    face_start_idx += len(section_indices)

            
            # Create mesh geometry
            if vertices and face_indices:
                apply_mesh_data(mesh_obj, vertices, normals, tangents, face_indices)
                
                # Apply UVs
                for uv_channel, uv_coords in uvs.items():
                    if uv_coords:
                        uv_layer = mesh_data.uv_layers.new(name=uv_channel)
                        for face in mesh_data.polygons:
                            for vert_idx, loop_idx in zip(face.vertices, face.loop_indices):
                                if vert_idx < len(uv_coords):
                                    uv_layer.data[loop_idx].uv = uv_coords[vert_idx]
                
                # Apply vertex colors
                if vertex_colors:
                    color_layer = mesh_data.vertex_colors.new(name="Col")
                    for face in mesh_data.polygons:
                        for vert_idx, loop_idx in zip(face.vertices, face.loop_indices):
                            if vert_idx < len(vertex_colors):
                                color_layer.data[loop_idx].color = vertex_colors[vert_idx]
            
            # Parent to appropriate group
            if mdn_mesh.groupIdx < len(groups):
                mesh_obj.parent = group_objects[mdn_mesh.groupIdx]
            
            skin_data = None
            if mdn_mesh.skinIdx < len(skins):
                skin_data = skins[mdn_mesh.skinIdx]
            
            # Set up armature if available
            if armature_obj and bone_weights and bone_indices:
                setup_bone_weights(mesh_obj, bone_weights, bone_indices, bones, skin_data)
                modifier = mesh_obj.modifiers.new(name="Armature", type='ARMATURE')
                modifier.object = armature_obj
            
            mesh_objects[mesh_idx] = mesh_obj
        
        return {'FINISHED'}


    def execute(self, context):
        return self.read_mdn_data(context, self.filepath)

def menu_func_import(self, context):
    self.layout.operator(ImportMDN.bl_idname, text="MGS4 Model (.mdn)")

def register():
    bpy.utils.register_class(ImportMDN)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)

def unregister():
    bpy.utils.unregister_class(ImportMDN)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)

if __name__ == "__main__":
    register()