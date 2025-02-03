import struct
import math
from dataclasses import dataclass
from typing import List

def strcode(string: str) -> int:
    id = 0
    mask = 0x00FFFFFF
    
    for c in string:
        id = ((id >> 19) | ((id << 5) & mask))
        id = (id + ord(c)) & mask
    
    return 1 if id == 0 else id

def strcode_from_name(name):
    try:
        if name.startswith("0x"):
            return int(name[2:], 16)
        else:
            return int(name, 16)
    except ValueError:
        return strcode(name)
        
def float_to_half(value):
    packed = struct.pack('f', value)
    bits = struct.unpack('I', packed)[0]
    
    sign = (bits >> 31) & 0x1
    exponent = (bits >> 23) & 0xff
    fraction = bits & 0x7fffff
    
    if exponent == 0:  # Zero or subnormal
        return (sign << 15)
    elif exponent == 0xff:  # Infinity or NaN
        if fraction == 0:  # Infinity
            return (sign << 15) | 0x7c00
        else:  # NaN
            return (sign << 15) | 0x7c00 | (fraction >> 13)
    
    exponent = exponent - 127 + 15
    
    if exponent < 0:
        return sign << 15
    elif exponent > 31:
        return (sign << 15) | 0x7c00
    
    return (sign << 15) | (exponent << 10) | (fraction >> 13)

def half_to_float(half):
    sign = (half >> 15) & 0x1
    exponent = (half >> 10) & 0x1f
    fraction = half & 0x3ff
    
    if exponent == 0:  # Zero or subnormal
        if fraction == 0:  # Zero
            packed = sign << 31
        else:  # Subnormal
            # Convert to normalized float
            fraction = fraction << 1
            while (fraction & 0x400) == 0:
                fraction = fraction << 1
                exponent -= 1
            fraction &= 0x3ff
            exponent += 1
            # Convert exponent bias
            exponent = exponent - 15 + 127
            packed = (sign << 31) | (exponent << 23) | (fraction << 13)
    elif exponent == 0x1f:  # Infinity or NaN
        packed = (sign << 31) | (0xff << 23) | (fraction << 13)
    else:  # Regular number
        # Convert exponent bias
        exponent = exponent - 15 + 127
        packed = (sign << 31) | (exponent << 23) | (fraction << 13)
    
    # Unpack bits to float
    return struct.unpack('f', struct.pack('I', packed))[0]
    
class BinaryReader:
    def __init__(self, data, little_endian=False):
        self.data = data
        self.offset = 0
        self.endian = '<' if little_endian else '>'
        self.length = len(data)
    
    def read_bytes(self, size):
        if self.offset + size > self.length:
            raise EOFError(f"Attempted to read {size} bytes at offset {self.offset}, but buffer length is {self.length:X}")
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result
    
    def read_uint8(self):
        return struct.unpack(f'B', self.read_bytes(1))[0]

    def read_int8(self):
        return struct.unpack(f'b', self.read_bytes(1))[0]

    def read_int32(self):
        return struct.unpack(f'{self.endian}i', self.read_bytes(4))[0]

    def read_uint32(self):
        return struct.unpack(f'{self.endian}I', self.read_bytes(4))[0]
    
    def read_uint16(self):
        return struct.unpack(f'{self.endian}H', self.read_bytes(2))[0]
    
    def read_float(self):
        return struct.unpack(f'{self.endian}f', self.read_bytes(4))[0]
        
    def read_half_float(self):
        half = self.read_uint16()
        value = half_to_float(half)
        return value

    def read_vec3(self):
        return struct.unpack(f'{self.endian}3f', self.read_bytes(12))
    
    def read_vec4(self):
        return struct.unpack(f'{self.endian}4f', self.read_bytes(16))
    
    def read_vec4_half(self):
        return (
            self.read_half_float(),
            self.read_half_float(),
            self.read_half_float(),
            self.read_half_float()
        )
    
    def seek(self, offset):
        if offset > self.length:
            raise EOFError(f"Attempted to seek to offset {offset}, but buffer length is {self.length:X}")
        self.offset = offset

import inspect

class BinaryWriter:
    def __init__(self, little_endian=False):
        self.data = bytearray()
        self.endian = '<' if little_endian else '>'
        self.offset = 0
    
    def write_bytes(self, data):
        if self.offset < len(self.data):
            self.data[self.offset:self.offset + len(data)] = data
        else:
            self.data.extend(data)
        self.offset += len(data)
    
    def write_uint32(self, value):
        self.write_bytes(struct.pack(f'{self.endian}I', value))
    
    def write_uint16(self, value):
        self.write_bytes(struct.pack(f'{self.endian}H', value))

    def write_uint16half(self, value):
        self.write_bytes(struct.pack(f'{self.endian}H', value))
    
    def write_uint8(self, value):
        self.write_bytes(struct.pack(f'{self.endian}B', value))

    def write_int8(self, value):
        self.write_bytes(struct.pack(f'{self.endian}b', value))

    def write_float(self, value):
        self.write_bytes(struct.pack(f'{self.endian}f', value))
        
    def write_half_float(self, value):
        half = float_to_half(value)
        self.write_uint16half(half)

    def write_vec3(self, x, y, z):
        self.write_bytes(struct.pack(f'{self.endian}3f', x, y, z))
    
    def write_vec4(self, x, y, z, w):
        self.write_bytes(struct.pack(f'{self.endian}4f', x, y, z, w))
    
    def write_vec4_half(self, x, y, z, w):
        self.write_half_float(x)
        self.write_half_float(y)
        self.write_half_float(z)
        self.write_half_float(w)

    def pad_to_alignment(self, alignment):
        while self.offset % alignment != 0:
            self.write_bytes(bytes([0]))
    
    def get_offset(self):
        return self.offset
    
    def seek(self, offset):
        if offset > len(self.data):
            self.data.extend(bytes(offset - len(self.data)))
        self.offset = offset
    
    def write_zeros(self, count):
        self.write_bytes(bytes(count))    

@dataclass
class MDN_Header:
    magic: int = 0x4D444E20  # 'MDN '
    filename: int = 0
    numBones: int = 0
    numGroups: int = 0
    numMesh: int = 0
    numFace: int = 0
    numVertexDefinition: int = 0
    numMaterial: int = 0
    numTexture: int = 0
    numSkin: int = 0
    boneOffset: int = 0
    groupOffset: int = 0
    meshOffset: int = 0
    faceOffset: int = 0
    vertexDefinitionOffset: int = 0
    materialOffset: int = 0
    textureOffset: int = 0
    skinOffset: int = 0
    vertexBufferOffset: int = 0
    vertexBufferSize: int = 0
    faceBufferOffset: int = 0
    faceBufferSize: int = 0
    pad: int = 0
    fileSize: int = 0
    max: List[float] = None
    min: List[float] = None

    def __post_init__(self):
        if self.max is None:
            self.max = [0.0, 0.0, 0.0, 1.0]
        if self.min is None:
            self.min = [0.0, 0.0, 0.0, 1.0]

    @classmethod
    def read(cls, reader):
        header = cls()

        header.magic = reader.read_uint32()
        print(f"magic: {header.magic:X}")

        header.filename = reader.read_uint32()
        print(f"filename: {header.filename:X}")

        header.numBones = reader.read_uint32()
        print(f"numBones: {header.numBones:X}")

        header.numGroups = reader.read_uint32()
        print(f"numGroups: {header.numGroups:X}")

        header.numMesh = reader.read_uint32()
        print(f"numMesh: {header.numMesh:X}")

        header.numFace = reader.read_uint32()
        print(f"numFace: {header.numFace:X}")

        header.numVertexDefinition = reader.read_uint32()
        print(f"numVertexDefinition: {header.numVertexDefinition:X}")

        header.numMaterial = reader.read_uint32()
        print(f"numMaterial: {header.numMaterial:X}")

        header.numTexture = reader.read_uint32()
        print(f"numTexture: {header.numTexture:X}")

        header.numSkin = reader.read_uint32()
        print(f"numSkin: {header.numSkin:X}")

        header.boneOffset = reader.read_uint32()
        print(f"boneOffset: {header.boneOffset:X}")

        header.groupOffset = reader.read_uint32()
        print(f"groupOffset: {header.groupOffset:X}")

        header.meshOffset = reader.read_uint32()
        print(f"meshOffset: {header.meshOffset:X}")

        header.faceOffset = reader.read_uint32()
        print(f"faceOffset: {header.faceOffset:X}")

        header.vertexDefinitionOffset = reader.read_uint32()
        print(f"vertexDefinitionOffset: {header.vertexDefinitionOffset:X}")

        header.materialOffset = reader.read_uint32()
        print(f"materialOffset: {header.materialOffset:X}")

        header.textureOffset = reader.read_uint32()
        print(f"textureOffset: {header.textureOffset:X}")

        header.skinOffset = reader.read_uint32()
        print(f"skinOffset: {header.skinOffset:X}")

        header.vertexBufferOffset = reader.read_uint32()
        print(f"vertexBufferOffset: {header.vertexBufferOffset:X}")

        header.vertexBufferSize = reader.read_uint32()
        print(f"vertexBufferSize: {header.vertexBufferSize:X}")

        header.faceBufferOffset = reader.read_uint32()
        print(f"faceBufferOffset: {header.faceBufferOffset:X}")

        header.faceBufferSize = reader.read_uint32()
        print(f"faceBufferSize: {header.faceBufferSize:X}")

        header.pad = reader.read_uint32()
        print(f"pad: {header.pad:X}")

        header.fileSize = reader.read_uint32()
        print(f"fileSize: {header.fileSize:X}")

        header.max = list(reader.read_vec4())
        print(f"max: {header.max}")

        header.min = list(reader.read_vec4())
        print(f"min: {header.min}")

        return header

    def write(self, writer):
        writer.write_uint32(self.magic)
        writer.write_uint32(self.filename)
        writer.write_uint32(self.numBones)
        writer.write_uint32(self.numGroups)
        writer.write_uint32(self.numMesh)
        writer.write_uint32(self.numFace)
        writer.write_uint32(self.numVertexDefinition)
        writer.write_uint32(self.numMaterial)
        writer.write_uint32(self.numTexture)
        writer.write_uint32(self.numSkin)
        writer.write_uint32(self.boneOffset)
        writer.write_uint32(self.groupOffset)
        writer.write_uint32(self.meshOffset)
        writer.write_uint32(self.faceOffset)
        writer.write_uint32(self.vertexDefinitionOffset)
        writer.write_uint32(self.materialOffset)
        writer.write_uint32(self.textureOffset)
        writer.write_uint32(self.skinOffset)
        writer.write_uint32(self.vertexBufferOffset)
        writer.write_uint32(self.vertexBufferSize)
        writer.write_uint32(self.faceBufferOffset)
        writer.write_uint32(self.faceBufferSize)
        writer.write_uint32(self.pad)
        writer.write_uint32(self.fileSize)
        writer.write_vec4(*self.max)
        writer.write_vec4(*self.min)

@dataclass
class MDN_Group:
    strcode: int = 0
    flag: int = 0
    parent: int = 0
    pad: int = 0

    @classmethod
    def read(cls, reader):
        cls.strcode = reader.read_uint32()
        print(f"strcode: {cls.strcode:X}")

        cls.flag = reader.read_uint32()
        print(f"flag: {cls.flag:X}")

        cls.parent = reader.read_uint32()
        print(f"parent: {cls.parent:X}")

        cls.pad = reader.read_uint32()
        print(f"pad: {cls.pad:X}")

        return cls(
            strcode=cls.strcode,
            flag=cls.flag ,
            parent=cls.parent,
            pad=cls.pad
        )

    def write(self, writer):
        writer.write_uint32(self.strcode)
        writer.write_uint32(self.flag)
        writer.write_uint32(self.parent)
        writer.write_uint32(self.pad)

@dataclass
class MDN_Mesh:
    groupIdx: int = 0
    flag: int = 0
    numFaceIdx: int = 0
    faceIdx: int = 0
    vertexDefIdx: int = 0
    skinIdx: int = 0
    numVertex: int = 0
    pad: int = 0
    max: List[float] = None
    min: List[float] = None
    pos: List[float] = None

    def __post_init__(self):
        if self.max is None:
            self.max = [0.0, 0.0, 0.0, 1.0]
        if self.min is None:
            self.min = [0.0, 0.0, 0.0, 1.0]
        if self.pos is None:
            self.pos = [0.0, 0.0, 0.0, 1.0]

    @classmethod
    def read(cls, reader):
        mesh = cls()
        mesh.groupIdx = reader.read_uint32()
        mesh.flag = reader.read_uint32()
        mesh.numFaceIdx = reader.read_uint32()
        mesh.faceIdx = reader.read_uint32()
        mesh.vertexDefIdx = reader.read_uint32()
        mesh.skinIdx = reader.read_uint32()
        mesh.numVertex = reader.read_uint32()
        mesh.pad = reader.read_uint32()
        mesh.max = list(reader.read_vec4())
        mesh.min = list(reader.read_vec4())
        mesh.pos = list(reader.read_vec4())
        return mesh

    def write(self, writer):
        writer.write_uint32(self.groupIdx)
        writer.write_uint32(self.flag)
        writer.write_uint32(self.numFaceIdx)
        writer.write_uint32(self.faceIdx)
        writer.write_uint32(self.vertexDefIdx)
        writer.write_uint32(self.skinIdx)
        writer.write_uint32(self.numVertex)
        writer.write_uint32(self.pad)
        writer.write_vec4(*self.max)
        writer.write_vec4(*self.min)
        writer.write_vec4(*self.pos)

@dataclass
class MDN_VertexDefinition:
    pad: int = 0
    defintionCount: int = 0
    stride: int = 0
    offset: int = 0
    definition: List[int] = None
    position: List[int] = None

    def __post_init__(self):
        if self.definition is None:
            self.definition = [0] * 16
        elif len(self.definition) < 16:
            self.definition.extend([0] * (16 - len(self.definition)))
            
        if self.position is None:
            self.position = [0] * 16
        elif len(self.position) < 16:
            self.position.extend([0] * (16 - len(self.position)))
            
        if self.defintionCount > 16:
            print(f"Warning: Invalid definition count {self.defintionCount}, clamping to 16")
            self.defintionCount = 16
            
        if self.stride < 12:
            print(f"Warning: Invalid stride {self.stride}, setting to minimum 12")
            self.stride = 12

    @classmethod
    def read(cls, reader):
        try:
            vdef = cls()
            vdef.pad = reader.read_uint32()
            vdef.defintionCount = reader.read_uint32()
            vdef.stride = reader.read_uint32()
            vdef.offset = reader.read_uint32()
            
            vdef.definition = list(reader.read_bytes(16))
            vdef.position = list(reader.read_bytes(16))
            
            vdef.__post_init__()
            
            print(f"\nRead vertex definition:")
            print(f"  Definition count: {vdef.defintionCount:X}")
            print(f"  Stride: {vdef.stride:X}")
            print(f"  Offset: {vdef.offset:X}")
            print(f"  Definition bytes: {[hex(x) for x in vdef.definition[:vdef.defintionCount]]}")
            print(f"  Position bytes: {[hex(x) for x in vdef.position[:vdef.defintionCount]]}")

            return vdef
            
        except Exception as e:
            print(f"Error reading vertex definition: {str(e):X}")
            return cls(defintionCount=1, stride=12)

    def write(self, writer):
        print(f"\nWriting vertex definition:")
        print(f"  Definition count: {self.defintionCount:X}")
        print(f"  Stride: {self.stride:X}")
        print(f"  Offset: {self.offset:X}")
        print(f"  Definition bytes: {[hex(x) for x in self.definition[:self.defintionCount]]}")
        print(f"  Position bytes: {[hex(x) for x in self.position[:self.defintionCount]]}")
        
        writer.write_uint32(self.pad)
        writer.write_uint32(self.defintionCount)
        writer.write_uint32(self.stride)
        writer.write_uint32(self.offset)
        writer.write_bytes(bytes(self.definition[:16]))
        writer.write_bytes(bytes(self.position[:16]))
        
@dataclass
class MDN_Face:
    type: int = 0
    count: int = 0
    offset: int = 0
    matGroup: int = 0
    start: int = 0
    size: int = 0

    @classmethod
    def read(cls, reader):
        return cls(
            type=reader.read_uint16(),
            count=reader.read_uint16(),
            offset=reader.read_uint32(),
            matGroup=reader.read_uint32(),
            start=reader.read_uint16(),
            size=reader.read_uint16()
        )

    def write(self, writer):
        writer.write_uint16(self.type)
        writer.write_uint16(self.count)
        writer.write_uint32(self.offset)
        writer.write_uint32(self.matGroup)
        writer.write_uint16(self.start)
        writer.write_uint16(self.size)

@dataclass
class MDN_Material:
    flag: int = 0
    strcode: int = 0
    
    textureCount: int = 0
    colorCount: int = 0

    diffuseIndex: int = 0
    normalIndex: int = 0
    specularIndex: int = 0
    filterIndex: int = 0
    ambientIndex: int = 0
    specGradientIndex: int = 0
    wrinkleIndex: int = 0
    unknownIndex: int = 0
    
    diffuse_color: tuple = (0.0, 0.0, 0.0, 1.0)
    specular_color: tuple = (0.0, 0.0, 0.0, 1.0)
    unknown_color1: tuple = (0.0, 0.0, 0.0, 1.0)
    unknown_color2: tuple = (0.0, 0.0, 0.0, 1.0)
    unknown_color3: tuple = (0.0, 0.0, 0.0, 1.0)
    unknown_color4: tuple = (0.0, 0.0, 0.0, 1.0)
    unknown_color5: tuple = (0.0, 0.0, 0.0, 1.0)
    unknown_color6: tuple = (0.0, 0.0, 0.0, 1.0)

    @classmethod
    def read(cls, reader):
        material = cls()
        
        material.flag = reader.read_uint32()
        material.strcode = reader.read_uint32()
        material.textureCount = reader.read_uint32()
        material.colorCount = reader.read_uint32()
        
        material.diffuseIndex = reader.read_uint32()
        material.normalIndex = reader.read_uint32()
        material.specularIndex = reader.read_uint32()
        material.filterIndex = reader.read_uint32()
        material.ambientIndex = reader.read_uint32()
        material.specGradientIndex = reader.read_uint32()
        material.wrinkleIndex = reader.read_uint32()
        material.unknownIndex = reader.read_uint32()
        
        material.diffuse_color = reader.read_vec4_half()
        material.specular_color = reader.read_vec4_half()
        material.unknown_color1 = reader.read_vec4_half()
        material.unknown_color2 = reader.read_vec4_half()
        material.unknown_color3 = reader.read_vec4_half()
        material.unknown_color4 = reader.read_vec4_half()
        material.unknown_color5 = reader.read_vec4_half()
        material.unknown_color6 = reader.read_vec4_half()
        
        return material

    def write(self, writer):
        writer.write_uint32(self.flag)
        writer.write_uint32(self.strcode)
        writer.write_uint32(self.textureCount)
        writer.write_uint32(self.colorCount)
        
        writer.write_uint32(self.diffuseIndex)
        writer.write_uint32(self.normalIndex)
        writer.write_uint32(self.specularIndex)
        writer.write_uint32(self.filterIndex)
        writer.write_uint32(self.ambientIndex)
        writer.write_uint32(self.specGradientIndex)
        writer.write_uint32(self.wrinkleIndex)
        writer.write_uint32(self.unknownIndex)
        
        writer.write_vec4_half(*self.diffuse_color)
        writer.write_vec4_half(*self.specular_color)
        writer.write_vec4_half(*self.unknown_color1)
        writer.write_vec4_half(*self.unknown_color2)
        writer.write_vec4_half(*self.unknown_color3)
        writer.write_vec4_half(*self.unknown_color4)
        writer.write_vec4_half(*self.unknown_color5)
        writer.write_vec4_half(*self.unknown_color6)
            
@dataclass
class MDN_Texture:
    strcode: int = 0
    flag: int = 0
    scaleU: float = 1.0
    scaleV: float = 1.0
    posU: float = 0.0
    posV: float = 0.0
    pad: List[int] = None

    def __post_init__(self):
        if self.pad is None:
            self.pad = [0] * 2

    @classmethod
    def read(cls, reader):
        texture = cls()
        texture.strcode = reader.read_uint32()
        texture.flag = reader.read_uint32()
        texture.scaleU = reader.read_float()
        texture.scaleV = reader.read_float()
        texture.posU = reader.read_float()
        texture.posV = reader.read_float()
        texture.pad = [reader.read_uint32() for _ in range(2)]
        return texture

    def write(self, writer):
        writer.write_uint32(self.strcode)
        writer.write_uint32(self.flag)
        writer.write_float(self.scaleU)
        writer.write_float(self.scaleV)
        writer.write_float(self.posU)
        writer.write_float(self.posV)
        for p in self.pad:
            writer.write_uint32(p)

@dataclass
class MDN_Bone:
    strcode: int = 0
    flag: int = 0
    parent: int = -1
    pad: int = 0
    parentPos: List[float] = None
    worldPos: List[float] = None  
    max: List[float] = None 
    min: List[float] = None 

    def __post_init__(self):
        if self.parentPos is None:
            self.parentPos = [0.0, 0.0, 0.0, 1.0]
        if self.worldPos is None:
            self.worldPos = [0.0, 0.0, 0.0, 1.0]
        if self.max is None:
            self.max = [0.0, 0.0, 0.0, 1.0]
        if self.min is None:
            self.min = [0.0, 0.0, 0.0, 1.0]

    @classmethod
    def read(cls, reader):
        bone = cls()
        bone.strcode = reader.read_uint32()
        bone.flag = reader.read_uint32()
        bone.parent = reader.read_int32()
        bone.pad = reader.read_uint32()
        bone.parentPos = list(reader.read_vec4())
        bone.worldPos = list(reader.read_vec4())
        bone.max = list(reader.read_vec4())
        bone.min = list(reader.read_vec4())
        return bone

    def write(self, writer):
        writer.write_uint32(self.strcode)
        writer.write_uint32(self.flag)
        writer.write_uint32(self.parent)
        writer.write_uint32(self.pad)
        writer.write_vec4(*self.parentPos)
        writer.write_vec4(*self.worldPos)
        writer.write_vec4(*self.max)
        writer.write_vec4(*self.min)

@dataclass
class MDN_Skin:
    unknown: int = 0
    count: int = 0
    nullBytes: int = 0
    boneId: List[int] = None

    def __post_init__(self):
        if self.boneId is None:
            self.boneId = [0] * 32

    @classmethod
    def read(cls, reader):
        skin = cls()
        skin.unknown = reader.read_uint32()
        skin.count = reader.read_uint16()
        skin.nullBytes = reader.read_uint16()
        skin.boneId = [reader.read_uint8() for _ in range(32)]
        return skin

    def write(self, writer):
        writer.write_uint32(self.unknown)
        writer.write_uint16(self.count)
        writer.write_uint16(self.nullBytes)
        for bone_id in self.boneId:
            writer.write_uint8(bone_id)

class MDN_Definition:
    POSITION = 0
    WEIGHT = 1
    NORMAL = 2
    COLOUR = 3
    UNKNOWNA = 4
    TEXTURE3DS = 5
    UNKNOWNC = 6
    BONEIDX = 7
    TEXTURE00 = 8
    TEXTURE01 = 9
    TEXTURE02 = 10
    TEXTURE03 = 11
    TEXTURE04 = 12
    TEXTURE05 = 13
    TANGENT = 14

class MDN_DataType:
    FLOAT = 0x01
    SHORT = 0x05
    HALFFLOAT = 0x07
    UBYTE = 0x08
    BYTE = 0x09
    FLOAT_COMPRESSED = 0x0A
    BYTE_COMPRESSED = 0x0B