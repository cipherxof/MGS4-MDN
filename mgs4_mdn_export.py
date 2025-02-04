import bpy
import bmesh
import mathutils
from bpy_extras.io_utils import ExportHelper
from bpy.props import StringProperty, BoolProperty
from bpy.types import Operator
from mgs4_mdn_shared import *

bl_info = {
    "name": "Metal Gear Solid 4 MDN Export",
    "author": "cipherxof",
    "version": (1, 0, 0),
    "blender": (4, 3, 2),
    "location": "File > Export > MGS4 MDN (.mdn)",
    "description": "Export MGS4 MDN format",
    "category": "Import-Export",
}

MODEL_EXPORT_SCALE = 1.0

def write_face_buffer(writer, mesh_obj):
    if not mesh_obj.data.loop_triangles:
        mesh_obj.data.calc_loop_triangles()
        
    start_offset = writer.offset
    mesh = mesh_obj.data
    
    for tri in mesh.loop_triangles:
        writer.write_uint16(tri.vertices[0])
        writer.write_uint16(tri.vertices[1])
        writer.write_uint16(tri.vertices[2])

    writer.pad_to_alignment(16)
    return writer.offset - start_offset

def get_vertex_weights(vertex, vertex_groups, bone_name_to_idx):
    weights = []
    
    for group in vertex.groups:
        group_name = vertex_groups[group.group].name
        if group_name.startswith('MeshGroup_'):
            continue
            
        weight = group.weight
        if weight > 0:
            bone_idx = bone_name_to_idx[group_name]
            weights.append((bone_idx, weight))

    weights.sort(key=lambda x: x[1], reverse=True)
    weights = weights[:4]
    
    total_weight = sum(w for _, w in weights)
    if total_weight > 0:
        weights = [(idx, w/total_weight) for idx, w in weights]
    
    while len(weights) < 4:
        pad_idx = weights[0][0] if weights else 0
        weights.append((pad_idx, 0.0))
        
    final_weights = [w for _, w in weights]
    final_indices = [i for i, _ in weights]
    
    return final_weights, final_indices

def create_skin_data(mesh_obj, bone_name_to_idx, max_bones=32):
    skin = MDN_Skin()
    skin.unknown = 4
    skin.nullBytes = 0
    
    skinned_names = mesh_obj.get("skinned_bones", [])

    bone_indices = set()
    for vg_name in skinned_names:
        vg = mesh_obj.vertex_groups.get(vg_name)
        if vg:
            bone_idx = bone_name_to_idx[vg.name]
            bone_indices.add(bone_idx)
    
    bone_indices = sorted(list(bone_indices))
    skin.count = len(bone_indices)
    
    skin.boneId = [0] * max_bones
    for i, bone_idx in enumerate(bone_indices):
        if i < max_bones:
            skin.boneId[i] = bone_idx
    
    return skin

def calculate_skin_indices(meshes, bone_name_to_idx):
    skin_lookup = {}
    skins = []
    
    for mesh_obj in meshes:
        bone_groups = []
        skinnable_names = mesh_obj.get("skinned_bones", [])

        for vg_name in skinnable_names:
            vg = mesh_obj.vertex_groups.get(vg_name)
            if vg:
                bone_groups.append(vg)

        if not bone_groups:
            mesh_obj["mdn_skin_index"] = 0
            continue
            
        vgroup_names = tuple(sorted(vg.name for vg in bone_groups))

        if vgroup_names in skin_lookup:
            mesh_obj["mdn_skin_index"] = skin_lookup[vgroup_names]
        else:
            skin = create_skin_data(mesh_obj, bone_name_to_idx)
            skin_lookup[vgroup_names] = len(skins)
            mesh_obj["mdn_skin_index"] = len(skins)
            skins.append(skin)

    return skins

def write_skins(writer, skins):
    writer.pad_to_alignment(16)
    skin_offset = writer.get_offset()
    
    for skin in skins:
        skin.write(writer)
    
    return skin_offset

def collect_bones(armature_obj):
    if not armature_obj or armature_obj.type != 'ARMATURE':
        return [], {}
       
    bones = []
    bone_name_to_idx = {}
    bone_to_idx = {}
   
    for idx, bone in enumerate(armature_obj.data.bones):
        if "bone_table_index" not in bone:
            print(f"Warning: bone {bone.name} has no table index, assigning the default value")
            bone["bone_table_index"] = idx

        mdn_bone = MDN_Bone()
        mdn_bone.strcode = strcode_from_name(bone.name)
        mdn_bone.flag = 0
        mdn_bone.parent = 0xFFFFFFFF
        mdn_bone.pad = 0
       
        head = bone.head_local
        tail = bone.tail_local
       
        mdn_bone.worldPos = [head.x, head.y, head.z, 1.0]
        mdn_bone.parentPos = [tail.x, tail.y, tail.z, 1.0]
       
        padding = 0.1
        min_bounds = [
            min(head.x, tail.x) - padding,
            min(head.y, tail.y) - padding,
            min(head.z, tail.z) - padding,
            1.0
        ]
        max_bounds = [
            max(head.x, tail.x) + padding,
            max(head.y, tail.y) + padding,
            max(head.z, tail.z) + padding,
            1.0
        ]
       
        mdn_bone.min = min_bounds
        mdn_bone.max = max_bounds
       
        bones.append(mdn_bone)
        bone_name_to_idx[bone.name] = bone["bone_table_index"]
        bone_to_idx[mdn_bone.strcode] = bone["bone_table_index"]
   
    # Set up parent relationships
    for idx, bone in enumerate(armature_obj.data.bones):
        if bone.parent and bone.parent.name in bone_name_to_idx:
            parent_idx = bone_name_to_idx[bone.parent.name]
            bones[idx].parent = parent_idx
           
            parent_world_pos = armature_obj.matrix_world @ bone.parent.head_local
            bones[idx].parentPos = [
                parent_world_pos.x,
                parent_world_pos.y,
                parent_world_pos.z,
                1.0
            ]
    
    bones.sort(key=lambda x: bone_to_idx[x.strcode])

    return bones, bone_name_to_idx

def collect_groups(meshes, armature_obj=None):
    groups = []
    mesh_to_group = {}
    group_name_to_idx = {}
    
    bone_strcodes = set()
    if armature_obj:
        for bone in armature_obj.data.bones:
            try:
                if bone.name.startswith("0x"):
                    bone_strcodes.add(int(bone.name[2:], 16))
                else:
                    bone_strcodes.add(int(bone.name, 16))
            except ValueError:
                bone_strcodes.add(abs(hash(bone.name)) & 0xFFFFFFFF)
    
    for mesh_obj in bpy.context.scene.objects:
        if mesh_obj.type == 'EMPTY' and mesh_obj.name.startswith("MeshGroup_"):
            try:
                strcode_str = mesh_obj.name.split("_")[1]
                strcode = int(strcode_str, 16)
                
                if strcode in bone_strcodes:
                    continue
                
                group = MDN_Group()
                group.strcode = strcode
                group.flag = 0
                group.parent = 0xFFFFFFFF
                group.pad = 0
                
                groups.append(group)
                group_name_to_idx[mesh_obj.name] = len(groups) - 1
                
            except ValueError:
                print(f"Warning: Invalid mesh group name format: {mesh_obj.name}")
                continue
    
    for mesh_obj in bpy.context.scene.objects:
        if mesh_obj.type == 'EMPTY' and mesh_obj.name.startswith("MeshGroup_"):
            if mesh_obj.name in group_name_to_idx:
                group_idx = group_name_to_idx[mesh_obj.name]
                
                if (mesh_obj.parent and mesh_obj.parent.type == 'EMPTY' and 
                    mesh_obj.parent.name.startswith("MeshGroup_") and 
                    mesh_obj.parent.name in group_name_to_idx):
                    groups[group_idx].parent = group_name_to_idx[mesh_obj.parent.name]
    
    for mesh_obj in meshes:
        if mesh_obj.parent and mesh_obj.parent.type == 'EMPTY' and mesh_obj.parent.name.startswith("MeshGroup_"):
            if mesh_obj.parent.name in group_name_to_idx:
                mesh_to_group[mesh_obj] = group_name_to_idx[mesh_obj.parent.name]
                
                current_parent = mesh_obj.parent
                while (current_parent and current_parent.type == 'EMPTY' and 
                       current_parent.name.startswith("MeshGroup_") and 
                       current_parent.name in group_name_to_idx):

                    if current_parent.name not in [vg.name for vg in mesh_obj.vertex_groups]:
                        mesh_obj.vertex_groups.new(name=current_parent.name)
                    current_parent = current_parent.parent
    
    if not groups:
        default_group = MDN_Group()
        default_group.strcode = 0x696969
        default_group.flag = 0
        default_group.parent = 0xFFFFFFFF
        default_group.pad = 0
        groups.append(default_group)
        
        for mesh_obj in meshes:
            if mesh_obj not in mesh_to_group:
                mesh_to_group[mesh_obj] = 0
    
    print("\nCollected Mesh Groups:")
    for i, group in enumerate(groups):
        print(f"Group {i}: strcode={hex(group.strcode)}, parent={group.parent}")
        
    return groups, mesh_to_group

def calculate_world_bounds(meshes):
    bounds_min = [float('inf')] * 3
    bounds_max = [float('-inf')] * 3
    
    unit_scale = bpy.context.scene.unit_settings.scale_length
    game_scale = 100.0 * unit_scale
    
    for mesh_obj in meshes:
        world_matrix = mesh_obj.matrix_world
        
        for vertex in mesh_obj.data.vertices:
            world_vertex = world_matrix @ vertex.co
            scaled_vertex = world_vertex * game_scale
            
            for i in range(3):
                bounds_min[i] = min(bounds_min[i], scaled_vertex[i])
                bounds_max[i] = max(bounds_max[i], scaled_vertex[i])
    
    padding = 10.0
    for i in range(3):
        bounds_min[i] -= padding
        bounds_max[i] += padding
    
    return bounds_min, bounds_max

def get_mesh_bounds(mesh_obj):
    bounds_min = [float('inf')] * 3
    bounds_max = [float('-inf')] * 3
    
    unit_scale = bpy.context.scene.unit_settings.scale_length
    game_scale = MODEL_EXPORT_SCALE * unit_scale
    
    world_matrix = mesh_obj.matrix_world
    for vertex in mesh_obj.data.vertices:
        world_co = world_matrix @ vertex.co
        scaled_co = world_co * game_scale
        
        for i in range(3):
            bounds_min[i] = min(bounds_min[i], scaled_co[i])
            bounds_max[i] = max(bounds_max[i], scaled_co[i])
    
    padding = 10.0
    for i in range(3):
        bounds_min[i] -= padding
        bounds_max[i] += padding
    
    return bounds_min, bounds_max

def calculate_vertex_offset(previous_meshes):
    offset = 0

    for mesh_obj in previous_meshes:
        definition_bytes, position_bytes, vertex_stride = create_vertex_definition(mesh_obj)
        num_vertices = len(mesh_obj.data.vertices)
        vertex_size = num_vertices * vertex_stride
        vertex_size = (vertex_size + 15) & ~15
        offset += vertex_size

    return offset

def get_face_material_index(mesh_obj, material_lookup):
    if mesh_obj.material_slots and mesh_obj.material_slots[0].material:
        material = mesh_obj.material_slots[0].material
        if material in material_lookup:
            return material_lookup[material]
        else:
            print(f"Warning: Material {material.name} not found in lookup")
    else:
        print(f"Warning: Mesh {mesh_obj.name} has no material assigned")
    return 0

def process_material_nodes(material, mdn_material, textures, texture_lookup):
    if not material.use_nodes:
        return
        
    princ_bsdf = None
    normal_map_node = None
    for node in material.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            princ_bsdf = node
        elif node.type == 'NORMAL_MAP':
            normal_map_node = node
            
    if not princ_bsdf:
        return

    used_textures = set()
        
    for node_input in princ_bsdf.inputs:
        if node_input.is_linked:
            from_node = node_input.links[0].from_node
            if from_node.type == 'TEX_IMAGE' and from_node.image:
                try:
                    tex_name = from_node.image.name.split('.')[0]
                    strcode = int(tex_name, 16)
                    used_textures.add(strcode)
                    
                    if strcode in texture_lookup:
                        tex_idx = texture_lookup[strcode]
                    else:
                        mdn_texture = MDN_Texture()
                        mdn_texture.strcode = strcode
                        mdn_texture.flag = 0
                        mdn_texture.scaleU = 1.0
                        mdn_texture.scaleV = 1.0
                        mdn_texture.posU = 0.0
                        mdn_texture.posV = 0.0
                        mdn_texture.pad = [0, 0]
                        
                        textures.append(mdn_texture)
                        tex_idx = len(textures) - 1
                        texture_lookup[strcode] = tex_idx
                    
                    input_name = node_input.name.lower()
                    if input_name == 'base color':
                        mdn_material.diffuseIndex = tex_idx
                    elif input_name == 'specular ior level':
                        mdn_material.specularIndex = tex_idx
                        
                except ValueError:
                    print(f"Warning: Texture name '{from_node.image.name}' is not a valid hex value")
    
    if normal_map_node and normal_map_node.inputs['Color'].is_linked:
        from_node = normal_map_node.inputs['Color'].links[0].from_node
        if from_node.type == 'TEX_IMAGE' and from_node.image:
            try:
                tex_name = from_node.image.name.split('.')[0]
                strcode = int(tex_name, 16)
                used_textures.add(strcode)
                
                if strcode in texture_lookup:
                    tex_idx = texture_lookup[strcode]
                else:
                    mdn_texture = MDN_Texture()
                    mdn_texture.strcode = strcode
                    mdn_texture.flag = 0
                    mdn_texture.scaleU = 1.0
                    mdn_texture.scaleV = 1.0
                    mdn_texture.posU = 0.0
                    mdn_texture.posV = 0.0
                    mdn_texture.pad = [0, 0]
                    
                    textures.append(mdn_texture)
                    tex_idx = len(textures) - 1
                    texture_lookup[strcode] = tex_idx
                
                mdn_material.normalIndex = tex_idx
                
            except ValueError:
                print(f"Warning: Normal texture name '{from_node.image.name}' is not a valid hex value")
    
    mdn_material.textureCount = len(used_textures)
    print(f"Total unique textures: {len(used_textures)}")
    
    if "mdn_diffuse_color" in material:
        mdn_material.diffuse_color = tuple(material["mdn_diffuse_color"])
    else:
        mdn_material.diffuse_color = (
            princ_bsdf.inputs['Base Color'].default_value[0],
            princ_bsdf.inputs['Base Color'].default_value[1],
            princ_bsdf.inputs['Base Color'].default_value[2],
            princ_bsdf.inputs['Alpha'].default_value
        )
    
    if "mdn_specular_color" in material:
        mdn_material.specular_color = tuple(material["mdn_specular_color"])
    else:
        mdn_material.specular_color = (
            princ_bsdf.inputs['Specular IOR Level'].default_value,
            princ_bsdf.inputs['Metallic'].default_value,
            princ_bsdf.inputs['Roughness'].default_value,
            1.0
        )
    
    if "mdn_unknown_color1" in material:
        mdn_material.unknown_color1 = tuple(material["mdn_unknown_color1"])
    if "mdn_unknown_color2" in material:
        mdn_material.unknown_color2 = tuple(material["mdn_unknown_color2"])
    if "mdn_unknown_color3" in material:
        mdn_material.unknown_color3 = tuple(material["mdn_unknown_color3"])
    if "mdn_unknown_color4" in material:
        mdn_material.unknown_color4 = tuple(material["mdn_unknown_color4"])
    if "mdn_unknown_color5" in material:
        mdn_material.unknown_color5 = tuple(material["mdn_unknown_color5"])
    if "mdn_unknown_color6" in material:
        mdn_material.unknown_color6 = tuple(material["mdn_unknown_color6"])
    
    mdn_material.colorCount = 8

def create_default_material(material):
    mdn_material = MDN_Material()
    try:
        mdn_material.strcode = int(material.name, 16)
    except ValueError:
        print(f"Warning: Material name '{material.name}' is not a valid hex value")
        mdn_material.strcode = 0
        
    mdn_material.flag = 0
    mdn_material.textureCount = 0
    mdn_material.colorCount = 8
    
    mdn_material.diffuseIndex = 0
    mdn_material.normalIndex = 0
    mdn_material.specularIndex = 0
    mdn_material.filterIndex = 0
    mdn_material.ambientIndex = 0
    mdn_material.specGradientIndex = 0
    mdn_material.wrinkleIndex = 0
    mdn_material.unknownIndex = 0
    
    mdn_material.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    mdn_material.specular_color = (0.5, 0.5, 0.5, 1.0)
    mdn_material.unknown_color1 = (0.0, 0.0, 0.0, 1.0)
    mdn_material.unknown_color2 = (0.0, 0.0, 0.0, 1.0)
    mdn_material.unknown_color3 = (0.0, 0.0, 0.0, 1.0)
    mdn_material.unknown_color4 = (0.0, 0.0, 0.0, 1.0)
    mdn_material.unknown_color5 = (0.0, 0.0, 0.0, 1.0)
    mdn_material.unknown_color6 = (0.0, 0.0, 0.0, 1.0)
    
    return mdn_material

def to_dec(a: int, b: int, c: int) -> int:
    return ((c & 0x3FF) << 22) | ((b & 0x7FF) << 11) | (a & 0x7FF)

def normalize_and_compress_vector(vector):
    if vector.length > 0:
        vector = vector.normalized()
    
    nx = int(vector.x * 1023.0)
    ny = int(vector.y * 1023.0)
    nz = int(vector.z * 511.0)
    
    nx = max(-1023, min(1023, nx))
    ny = max(-1023, min(1023, ny))
    nz = max(-511, min(511, nz))
    
    packed = to_dec(nx, ny, nz)
    return packed

def create_vertex_definition(mesh_obj):
    definition = []
    position = []
    current_offset = 0
    mesh = mesh_obj.data

    # 1. Position
    definition.append(MDN_DataType.FLOAT << 4 | MDN_Definition.POSITION)
    position.append(current_offset)
    current_offset += 12

    # 2. Weight
    if mesh_obj.vertex_groups and any(v.groups for v in mesh.vertices):
        definition.append(MDN_DataType.UBYTE << 4 | MDN_Definition.WEIGHT)
        position.append(current_offset)
        current_offset += 4

    # 3. Normal
    definition.append(MDN_DataType.FLOAT_COMPRESSED << 4 | MDN_Definition.NORMAL)
    position.append(current_offset)
    current_offset += 4

    # 4. BoneIdx
    if mesh_obj.vertex_groups and any(v.groups for v in mesh.vertices):
        definition.append(MDN_DataType.BYTE << 4 | MDN_Definition.BONEIDX)
        position.append(current_offset)
        current_offset += 4

    # 5. Tangent
    if mesh.uv_layers:
        definition.append(MDN_DataType.FLOAT_COMPRESSED << 4 | MDN_Definition.TANGENT)
        position.append(current_offset)
        current_offset += 4

    # 6. UV Layers (up to 6 channels)
    if mesh.uv_layers:
        # Ensure alignment for UV data
        current_offset = (current_offset + 3) & ~3

        for i in range(min(len(mesh.uv_layers), 6)):  # Maximum of 6 UV channels
            definition.append(MDN_DataType.HALFFLOAT << 4 | (MDN_Definition.TEXTURE00 + i))
            position.append(current_offset)
            current_offset += 4  # Each UV pair is 2 half-floats = 4 bytes

    # Pad out the definition and positions to 16 entries each.
    while len(definition) < 16:
        definition.append(0)
    while len(position) < 16:
        position.append(0)

    stride = (current_offset + 3) & ~3
    return definition, position, stride

def calculate_tangents(mesh_obj):
    mesh = mesh_obj.data
    mesh.calc_tangents()
    
    vertex_tangents = {}
    vertex_counts = {}

    for poly in mesh.polygons:
        for loop_idx in poly.loop_indices:
            vertex_idx = mesh.loops[loop_idx].vertex_index
            tangent = mesh.loops[loop_idx].tangent

            if vertex_idx not in vertex_tangents:
                vertex_tangents[vertex_idx] = mathutils.Vector((0, 0, 0))
                vertex_counts[vertex_idx] = 0

            vertex_tangents[vertex_idx] += tangent
            vertex_counts[vertex_idx] += 1

    final_tangents = {}
    for vertex_idx, tangent_sum in vertex_tangents.items():
        count = vertex_counts[vertex_idx]
        if count > 0:
            avg_tangent = tangent_sum / count
            if avg_tangent.length > 0:
                avg_tangent.normalize()
            final_tangents[vertex_idx] = avg_tangent

    return final_tangents

def calculate_normals(mesh_obj):
    mesh = mesh_obj.data

    loop_normals = [0.0] * (len(mesh.loops) * 3)
    mesh.loops.foreach_get("normal", loop_normals)

    vertex_normal_sums = {}
    vertex_normal_counts = {}
    for poly in mesh.polygons:
        for loop_idx, vertex_idx in zip(poly.loop_indices, poly.vertices):
            i = loop_idx * 3
            normal = mathutils.Vector((
                loop_normals[i],
                loop_normals[i + 1],
                loop_normals[i + 2]
            ))
            if vertex_idx not in vertex_normal_sums:
                vertex_normal_sums[vertex_idx] = mathutils.Vector((0, 0, 0))
                vertex_normal_counts[vertex_idx] = 0
            vertex_normal_sums[vertex_idx] += normal
            vertex_normal_counts[vertex_idx] += 1

    vertex_normals = {}
    for vertex_idx, sum_normal in vertex_normal_sums.items():
        count = vertex_normal_counts[vertex_idx]
        avg_normal = sum_normal / count
        if avg_normal.length > 0:
            avg_normal.normalize()
        vertex_normals[vertex_idx] = avg_normal

    return vertex_normals

def write_vertex_data(writer, mesh_obj, vertex_def, bone_name_to_idx):
    definition_bytes, position_bytes, stride = vertex_def
    base_offset = writer.offset
    mesh = mesh_obj.data

    unit_scale = bpy.context.scene.unit_settings.scale_length
    game_scale = MODEL_EXPORT_SCALE * unit_scale

    if mesh.uv_layers:
        mesh.calc_tangents()

    vertex_tangents = calculate_tangents(mesh_obj)
    vertex_normals = calculate_normals(mesh_obj)

    vertex_uvs = {}
    if mesh.uv_layers:
        for uv_layer in mesh.uv_layers:
            for loop in mesh.loops:
                vertex_idx = loop.vertex_index
                if vertex_idx not in vertex_uvs:
                    vertex_uvs[vertex_idx] = {}
                uv_data = uv_layer.data[loop.index].uv
                vertex_uvs[vertex_idx][uv_layer.name] = (uv_data[0], uv_data[1])

    writer.pad_to_alignment(16)
    end_pos = 0

    for vertex_idx, vertex in enumerate(mesh.vertices):
        vertex_offset = base_offset + (vertex_idx * stride)
        
        weights, bone_indices = get_vertex_weights(vertex, mesh_obj.vertex_groups, bone_name_to_idx) if vertex.groups else (None, None)
        
        for def_idx, def_byte in enumerate(definition_bytes):
            if def_byte == 0:
                continue

            component_type = def_byte & 0x0F
            pos = position_bytes[def_idx]
            writer.seek(vertex_offset + pos)

            if component_type == MDN_Definition.POSITION:
                world_vertex = mesh_obj.matrix_world @ vertex.co
                scaled_vertex = world_vertex * game_scale
                writer.write_vec3(scaled_vertex.x, scaled_vertex.y, scaled_vertex.z)

            elif component_type == MDN_Definition.NORMAL:
                normal = vertex_normals.get(vertex_idx, mathutils.Vector((0, 0, 1)))
                writer.write_uint32(normalize_and_compress_vector(normal))

            elif component_type == MDN_Definition.TANGENT:
                tangent = vertex_tangents.get(vertex_idx, mathutils.Vector((1, 0, 0)))
                writer.write_uint32(normalize_and_compress_vector(tangent))

            elif (component_type >= MDN_Definition.TEXTURE00 and
                  component_type <= MDN_Definition.TEXTURE05):
                uv_idx = component_type - MDN_Definition.TEXTURE00
                if uv_idx < len(mesh.uv_layers):
                    uv_layer = mesh.uv_layers[uv_idx]
                    if vertex_idx in vertex_uvs and uv_layer.name in vertex_uvs[vertex_idx]:
                        u, v = vertex_uvs[vertex_idx][uv_layer.name]
                        writer.write_half_float(u)
                        writer.write_half_float(-v)
                    else:
                        writer.write_half_float(0.0)
                        writer.write_half_float(0.0)
                else:
                    writer.write_half_float(0.0)
                    writer.write_half_float(0.0)

            elif component_type == MDN_Definition.WEIGHT:
                if weights:
                    for weight in weights:
                        writer.write_uint8(int(weight * 255))
                else:
                    for _ in range(4):
                        writer.write_uint8(0)
                        
            elif component_type == MDN_Definition.BONEIDX:
                if bone_indices:
                    for idx in bone_indices:
                        writer.write_int8(idx)
                else:
                    for _ in range(4):
                        writer.write_int8(0)

            if writer.offset > end_pos:
                end_pos = writer.offset

    writer.seek(end_pos)
    writer.pad_to_alignment(16)

class ExportMDN(Operator, ExportHelper):
    bl_idname = "export_mesh.mdn"
    bl_label = "Export MDN"
    filename_ext = ".mdn"

    filter_glob: StringProperty(
        default="*.mdn",
        options={'HIDDEN'},
    )

    def write_mdn(self, context, filepath):
        writer = BinaryWriter(little_endian=False)
        
        meshes = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
        if not meshes:
            self.report({'ERROR'}, "No meshes selected")
            return {'CANCELLED'}

        armature_obj = None
        for obj in bpy.context.selected_objects:
            if obj.type == 'ARMATURE':
                armature_obj = obj
                break
        if not armature_obj:
            for mesh_obj in meshes:
                if mesh_obj.parent and mesh_obj.parent.type == 'ARMATURE':
                    armature_obj = mesh_obj.parent
                    break
                for modifier in mesh_obj.modifiers:
                    if modifier.type == 'ARMATURE' and modifier.object:
                        armature_obj = modifier.object
                        break
                if armature_obj:
                    break

        bones, bone_name_to_idx = collect_bones(armature_obj) if armature_obj else ([], {})
        groups, mesh_to_group = collect_groups(meshes, armature_obj)
        bounds_min, bounds_max = calculate_world_bounds(meshes)

        skins = []
        if bones:
            skins = calculate_skin_indices(meshes, bone_name_to_idx)

        materials = []
        textures = []
        material_lookup = {}
        texture_lookup = {}

        print("\nProcessing materials and textures...")
        for mesh_obj in meshes:
            for material_slot in mesh_obj.material_slots:
                if material_slot.material and material_slot.material not in material_lookup:
                    material = material_slot.material
                    print(f"\nProcessing material: {material.name}")
                    
                    if "mdn_flag" in material:
                        mdn_material = MDN_Material()
                        mdn_material.flag = material["mdn_flag"]
                        mdn_material.strcode = int(material.name, 16)
                        mdn_material.numTexture = 0 
                        mdn_material.colorCount = 8
                        mdn_material.texture = []
                    
                    else:
                        print(f"  Creating new MDN material")
                        mdn_material = create_default_material(material)
                    
                    print(f"mdn_material {mdn_material.strcode:X}")

                    process_material_nodes(material, mdn_material, textures, texture_lookup)
                    
                    materials.append(mdn_material)
                    material_lookup[material] = len(materials) - 1
                    
        # Update face material indices
        faces = []
        current_face_offset = 0
        for mesh_obj in meshes:
            mat_group = get_face_material_index(mesh_obj, material_lookup)
            face = MDN_Face(
                type=0x8000,
                count=len(mesh_obj.data.loop_triangles) * 3,
                offset=current_face_offset,
                matGroup=mat_group,
                start=0,
                size=len(mesh_obj.data.vertices)
            )
            faces.append(face)
            current_face_offset += ((len(mesh_obj.data.loop_triangles) * 6 + 15) & ~15)

        # Initialize header
        header = MDN_Header()
        header.magic = 0x4D444E20  # 'MDN '
        header.filename = 0x0A8396
        header.numBones = len(bones)
        header.numGroups = len(groups)
        header.numMesh = len(meshes)
        header.numFace = len(meshes)
        header.numVertexDefinition = len(meshes)
        header.min = bounds_min + [1.0]
        header.max = bounds_max + [1.0]
        header_start = writer.get_offset()
        header.write(writer)
        
        # 1. Write bones
        writer.pad_to_alignment(16)
        header.boneOffset = writer.get_offset()
        for bone in bones:
            bone.write(writer)
        
        # 2. Write groups
        writer.pad_to_alignment(16)
        header.groupOffset = writer.get_offset()
        for group in groups:
            group.write(writer)
        
        # 3. Write mesh data
        writer.pad_to_alignment(16)
        header.meshOffset = writer.get_offset()
        for mesh_obj in meshes:
            mesh = mesh_obj.data
            bounds_min, bounds_max = get_mesh_bounds(mesh_obj)
            mdn_mesh = MDN_Mesh(
                groupIdx=mesh_to_group.get(mesh_obj, 0),
                flag=0,
                numFaceIdx=1,
                faceIdx=meshes.index(mesh_obj),
                vertexDefIdx=meshes.index(mesh_obj),
                skinIdx=mesh_obj.get("mdn_skin_index", 0),
                numVertex=len(mesh.vertices),
                pad=0,
                max=bounds_max + [1.0],
                min=bounds_min + [1.0],
                pos=list(mesh_obj.location) + [1.0]
            )
            mdn_mesh.write(writer)
        
        # 4. Write face indices
        writer.pad_to_alignment(16)
        header.faceOffset = writer.get_offset()
        for face in faces:
            face.write(writer)
        
        # 5. Write vertex definitions
        writer.pad_to_alignment(16)
        header.vertexDefinitionOffset = writer.get_offset()
        for mesh_obj in meshes:
            definition_bytes, position_bytes, stride = create_vertex_definition(mesh_obj)
            vertex_def = MDN_VertexDefinition(
                pad=0,
                defintionCount=sum(1 for x in definition_bytes if x != 0),
                stride=stride,
                offset=calculate_vertex_offset(meshes[:meshes.index(mesh_obj)])
            )
            vertex_def.definition = definition_bytes
            vertex_def.position = position_bytes
            vertex_def.write(writer)

        # 6. Write materials and collect textures
        writer.pad_to_alignment(16)
        header.materialOffset = writer.get_offset()
        header.numMaterial = len(materials)
        for material in materials:
            material.write(writer)
        
        # 7. Write textures
        writer.pad_to_alignment(16)
        header.textureOffset = writer.get_offset()
        header.numTexture = len(textures)
        for texture in textures:
            texture.write(writer)
        
        # 8. Write skins
        writer.pad_to_alignment(16)
        if bones:
            header.skinOffset = write_skins(writer, skins)
            header.numSkin = len(skins)
        else:
            header.numSkin = 0
        
        # 9. Write vertex buffer
        writer.pad_to_alignment(16)
        header.vertexBufferOffset = writer.get_offset()
        for mesh_obj in meshes:
            write_vertex_data(writer, mesh_obj, create_vertex_definition(mesh_obj), bone_name_to_idx)
        header.vertexBufferSize = writer.get_offset() - header.vertexBufferOffset
        
        # 10. Write face buffer
        writer.pad_to_alignment(16)
        header.faceBufferOffset = writer.get_offset()
        total_face_buffer_size = 0

        for mesh_obj in meshes:
            face_size = write_face_buffer(writer, mesh_obj)
            total_face_buffer_size += face_size

        header.faceBufferSize = total_face_buffer_size
        
        # Update header
        header.fileSize = writer.get_offset()
        writer.seek(header_start)
        header.write(writer)
        writer.seek(header.fileSize)
        
        # Write the file
        with open(filepath, 'wb') as f:
            f.write(writer.data)
        
        return {'FINISHED'}
        
    def execute(self, context):
        return self.write_mdn(context, self.filepath)

def menu_func_export(self, context):
    self.layout.operator(ExportMDN.bl_idname, text="MGS4 Model (.mdn)")

def register():
    bpy.utils.register_class(ExportMDN)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)

def unregister():
    bpy.utils.unregister_class(ExportMDN)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)

if __name__ == "__main__":
    register()