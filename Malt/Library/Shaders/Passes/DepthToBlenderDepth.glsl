//Copyright (c) 2020 BlenderNPR and contributors. MIT license.

#include "Common.glsl"

#ifdef VERTEX_SHADER
void main()
{
    POSITION = in_position;
    UV[0] = in_position.xy * 0.5 + 0.5;

    gl_Position = vec4(POSITION, 1);
}
#endif

#ifdef PIXEL_SHADER

uniform sampler2D DEPTH_TEXTURE;

layout (location = 0) out vec4 OUT_RESULT;

void main()
{
    float depth = texture(DEPTH_TEXTURE, UV[0]).r;
    vec3 camera = screen_to_camera(UV[0], depth);
    OUT_RESULT.r = -camera.z;
}

#endif //PIXEL_SHADER
