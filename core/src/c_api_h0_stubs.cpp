// c_api_h0_stubs.cpp — Horizon 0 foundation ABI surface (see docs/H0-FRAMEWORK.md).
//
// U1 lands the *contract*: the symbols exist and the library links, but each
// capability reports DC_ERR_UNSUPPORTED until its owning unit replaces the stub
// (U2 document, U3 geometry, U5 real-time). This keeps the boundary honest — a
// caller gets a clear "not implemented yet" status, never a silent success or a
// crash. When a unit lands, it removes the corresponding stub here and provides
// the real definition; the declarations in c_api.h are the stable interface.

#include "designcore/c_api.h"

#ifndef DC_H0_HAVE_DOC          // U2 defines this when it provides the real impl
DC_API std::int32_t dc_doc_open(const char*, DcDocHandle* out) {
    if (out) *out = nullptr;
    return DC_ERR_UNSUPPORTED;
}
DC_API std::int32_t dc_doc_save(DcDocHandle, const char*) { return DC_ERR_UNSUPPORTED; }
DC_API std::int32_t dc_doc_version(DcDocHandle) { return DC_ERR_UNSUPPORTED; }
DC_API void         dc_doc_destroy(DcDocHandle) {}
#endif

#ifndef DC_H0_HAVE_GEOMETRY     // U3 defines this when OCCT geometry is provided
DC_API std::int32_t dc_brep_load_step(const char*, DcShapeHandle* out) {
    if (out) *out = nullptr;
    return DC_ERR_UNSUPPORTED;
}
DC_API std::int32_t dc_brep_tessellate(DcShapeHandle, double, DcMesh* out) {
    if (out) *out = DcMesh{0, 0, nullptr, nullptr, nullptr};
    return DC_ERR_UNSUPPORTED;
}
DC_API std::int32_t dc_brep_semantic_assembly(DcShapeHandle, const char*, const char*,
                                               DcSemanticAssemblyJson* out) {
    if (out) *out = DcSemanticAssemblyJson{0, nullptr};
    return DC_ERR_UNSUPPORTED;
}
DC_API std::int32_t dc_assembly_flatten(DcDocHandle, DcMesh* out) {
    if (out) *out = DcMesh{0, 0, nullptr, nullptr, nullptr};
    return DC_ERR_UNSUPPORTED;
}
DC_API void         dc_shape_destroy(DcShapeHandle) {}
#endif

#ifndef DC_H0_HAVE_REALTIME     // U5 defines this when Jolt is provided
DC_API std::int32_t dc_rt_world_new(DcDocHandle, DcRtHandle* out) {
    if (out) *out = nullptr;
    return DC_ERR_UNSUPPORTED;
}
DC_API std::int32_t dc_rt_step(DcRtHandle, double) { return DC_ERR_UNSUPPORTED; }
DC_API std::int32_t dc_rt_body_xform(DcRtHandle, const char*, double*) { return DC_ERR_UNSUPPORTED; }
DC_API void         dc_rt_world_destroy(DcRtHandle) {}
#endif
