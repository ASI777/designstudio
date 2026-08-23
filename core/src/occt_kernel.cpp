// Open CASCADE geometry boundary: real STEP loading and deterministic
// tessellation for the stable C API declared in designcore/c_api.h.
#include "designcore/c_api.h"

#ifdef HAS_OCCT

#include <BRepCheck_Analyzer.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <BRep_Tool.hxx>
#include <Poly_Triangulation.hxx>
#include <Quantity_ColorRGBA.hxx>
#include <STEPCAFControl_Reader.hxx>
#include <STEPControl_Reader.hxx>
#include <TDataStd_Name.hxx>
#include <TDF_LabelSequence.hxx>
#include <TDF_Tool.hxx>
#include <TDF_Reference.hxx>
#include <TDocStd_Document.hxx>
#include <TopAbs_Orientation.hxx>
#include <TopExp_Explorer.hxx>
#include <TopLoc_Location.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Shape.hxx>
#include <XCAFDoc_ColorTool.hxx>
#include <XCAFDoc_DocumentTool.hxx>
#include <XCAFDoc_MaterialTool.hxx>
#include <XCAFDoc_Material.hxx>
#include <XCAFDoc.hxx>
#include <XCAFDoc_ShapeTool.hxx>
#include <TCollection_AsciiString.hxx>
#include <gp_Pnt.hxx>
#include <gp_Vec.hxx>

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <limits>
#include <new>
#include <sstream>
#include <string>
#include <vector>

namespace {

struct ComponentState {
    TopoDS_Shape shape;
    std::string semanticId;
    std::string parentId;
    std::string name;
    std::string referenceDesignator;
    std::string material;
    double color[4] = {0.5, 0.5, 0.5, 1.0};
    gp_Trsf placement;
    std::int64_t triangleStart = -1;
    std::int64_t triangleCount = 0;
};

std::string materialName(const TDF_Label& label, const TDF_Label& documentLabel)
{
    Handle(TDF_Reference) reference;
    if (label.FindAttribute(XCAFDoc::MaterialRefGUID(), reference)
        && !reference.IsNull()) {
        Handle(XCAFDoc_Material) material;
        if (reference->Get().FindAttribute(XCAFDoc_Material::GetID(), material)
            && !material.IsNull() && !material->GetName().IsNull())
            return material->GetName()->ToCString();
    }
    const double density = XCAFDoc_MaterialTool::GetDensityForShape(label);
    if (!(density > 0.0) || !std::isfinite(density)) return "unknown";
    const Handle(XCAFDoc_MaterialTool) materials =
        XCAFDoc_DocumentTool::MaterialTool(documentLabel);
    if (materials.IsNull()) return "unknown";
    TDF_LabelSequence labels;
    materials->GetMaterialLabels(labels);
    for (Standard_Integer index = 1; index <= labels.Length(); ++index) {
        Handle(TCollection_HAsciiString) name, description, densityName, densityType;
        Standard_Real value = 0.0;
        if (XCAFDoc_MaterialTool::GetMaterial(labels.Value(index), name, description,
                                              value, densityName, densityType)
            && std::abs(value - density * 1000.0) <= 1.0e-6 && !name.IsNull())
            return name->ToCString();
    }
    return "unknown";
}

struct ShapeState {
    TopoDS_Shape shape;
    std::vector<float> vertices;
    std::vector<std::int32_t> indices;
    std::vector<std::int32_t> triangleOwners;
    std::vector<ComponentState> components;
    std::string semanticJson;
    bool semanticAp242 = false;
};

bool safeFloat(double value)
{
    return value >= -std::numeric_limits<float>::max()
        && value <= std::numeric_limits<float>::max();
}

std::string jsonEscape(const std::string& value)
{
    std::string escaped;
    escaped.reserve(value.size() + 8);
    for (const unsigned char character : value) {
        switch (character) {
        case '"': escaped += "\\\""; break;
        case '\\': escaped += "\\\\"; break;
        case '\b': escaped += "\\b"; break;
        case '\f': escaped += "\\f"; break;
        case '\n': escaped += "\\n"; break;
        case '\r': escaped += "\\r"; break;
        case '\t': escaped += "\\t"; break;
        default:
            if (character < 0x20) {
                std::ostringstream code;
                code << "\\u00" << std::hex << static_cast<int>(character);
                escaped += code.str();
            } else escaped.push_back(static_cast<char>(character));
        }
    }
    return escaped;
}

std::string labelEntry(const TDF_Label& label)
{
    TCollection_AsciiString entry;
    TDF_Tool::Entry(label, entry);
    std::string value = entry.ToCString();
    for (char& character : value)
        if (!(std::isalnum(static_cast<unsigned char>(character)) || character == '_'))
            character = '_';
    return value.empty() ? std::string("root") : value;
}

std::string labelName(const TDF_Label& label)
{
    Handle(TDataStd_Name) attribute;
    if (label.FindAttribute(TDataStd_Name::GetID(), attribute) && !attribute.IsNull()) {
        TCollection_AsciiString value(attribute->Get(), '?');
        if (!value.IsEmpty()) return value.ToCString();
    }
    return {};
}

bool looksAp242(const std::string& path)
{
    std::ifstream input(path, std::ios::binary);
    if (!input) return false;
    std::string header(128 * 1024, '\0');
    input.read(header.data(), static_cast<std::streamsize>(header.size()));
    header.resize(static_cast<std::size_t>(input.gcount()));
    return header.find("242") != std::string::npos;
}

void appendXcafLeaves(const TDF_Label& label, const TDF_Label& documentLabel,
                      const std::string& parentId,
                      std::vector<ComponentState>& components)
{
    if (!XCAFDoc_ShapeTool::IsShape(label)) return;
    const std::string semanticId = "step_" + labelEntry(label);
    const std::string name = labelName(label).empty() ? semanticId : labelName(label);
    TDF_LabelSequence children;
    if (XCAFDoc_ShapeTool::IsAssembly(label)
        && XCAFDoc_ShapeTool::GetComponents(label, children, Standard_False)
        && children.Length() > 0) {
        for (Standard_Integer index = 1; index <= children.Length(); ++index)
            appendXcafLeaves(children.Value(index), documentLabel, semanticId, components);
        return;
    }
    TopoDS_Shape shape = XCAFDoc_ShapeTool::GetShape(label);
    if (shape.IsNull()) return;
    ComponentState component;
    component.shape = std::move(shape);
    component.semanticId = semanticId;
    component.parentId = parentId;
    component.name = name;
    component.referenceDesignator = labelName(label);
    component.material = materialName(label, documentLabel);
    component.placement = XCAFDoc_ShapeTool::GetLocation(label).Transformation();
    Quantity_ColorRGBA color;
    // STEPCAF assigns appearance through a color-type link on the shape
    // label.  The untyped overload only reads a color-table label, so use the
    // generated-color link first and retain a shape-level fallback.
    if (XCAFDoc_ColorTool::GetColor(label, XCAFDoc_ColorGen, color)
        || XCAFDoc_ColorTool::GetColor(label, XCAFDoc_ColorSurf, color)
        || XCAFDoc_ColorTool::GetColor(label, XCAFDoc_ColorCurv, color)) {
        component.color[0] = color.GetRGB().Red();
        component.color[1] = color.GetRGB().Green();
        component.color[2] = color.GetRGB().Blue();
        component.color[3] = color.Alpha();
    }
    components.push_back(std::move(component));
}

bool loadXcafComponents(const std::string& path, ShapeState& state)
{
    if (!looksAp242(path)) return false;
    Handle(TDocStd_Document) document = new TDocStd_Document("DesignStudioAP242");
    STEPCAFControl_Reader reader;
    reader.SetColorMode(Standard_True);
    reader.SetNameMode(Standard_True);
    reader.SetMatMode(Standard_True);
    if (reader.ReadFile(path.c_str()) != IFSelect_RetDone || !reader.Transfer(document)) {
        return false;
    }
    const Handle(XCAFDoc_ShapeTool) shapeTool = XCAFDoc_DocumentTool::ShapeTool(document->Main());
    if (shapeTool.IsNull()) return false;
    TDF_LabelSequence roots;
    shapeTool->GetFreeShapes(roots);
    for (Standard_Integer index = 1; index <= roots.Length(); ++index)
        appendXcafLeaves(roots.Value(index), document->Main(), "assembly_root", state.components);
    if (state.components.empty()) {
        shapeTool->GetShapes(roots);
        for (Standard_Integer index = 1; index <= roots.Length(); ++index)
            appendXcafLeaves(roots.Value(index), document->Main(), "assembly_root", state.components);
    }
    // Use the XCAF-transferred compound for tessellation when it is valid.
    // Its face identities are the same TShapes held by the component labels;
    // matching a separately transferred STEPControl shape would lose that
    // ownership relationship even when the geometry is numerically equal.
    const TopoDS_Shape assemblyShape = shapeTool->GetOneShape();
    if (!assemblyShape.IsNull() && BRepCheck_Analyzer(assemblyShape).IsValid())
        state.shape = assemblyShape;
    state.semanticAp242 = !state.components.empty();
    return state.semanticAp242;
}

int componentForFace(const ShapeState& state, const TopoDS_Face& face)
{
    for (std::size_t index = 0; index < state.components.size(); ++index) {
        for (TopExp_Explorer explorer(state.components[index].shape, TopAbs_FACE);
             explorer.More(); explorer.Next()) {
            const TopoDS_Face candidate = TopoDS::Face(explorer.Current());
            if (candidate.IsSame(face) || candidate.IsPartner(face))
                return static_cast<int>(index);
        }
    }
    return -1;
}

bool buildSemanticJson(ShapeState& state, const char* sourceSha, const char* tessellationSha)
{
    if (!state.semanticAp242 || !sourceSha || !tessellationSha
        || state.triangleOwners.size() != state.indices.size() / 3) {
        return false;
    }
    for (std::size_t index = 0; index < state.triangleOwners.size(); ++index) {
        const int owner = state.triangleOwners[index];
        if (owner < 0 || owner >= static_cast<int>(state.components.size())) {
            return false;
        }
        ComponentState& component = state.components[owner];
        if (component.triangleStart < 0) component.triangleStart = static_cast<std::int64_t>(index);
        if (component.triangleStart + component.triangleCount != static_cast<std::int64_t>(index)) {
            return false; // A v2 component has one contiguous ownership range.
        }
        ++component.triangleCount;
    }
    std::vector<int> order;
    order.reserve(state.components.size());
    for (int index = 0; index < static_cast<int>(state.components.size()); ++index) {
        if (state.components[index].triangleStart < 0 || state.components[index].triangleCount <= 0)
            return false;
        order.push_back(index);
    }
    std::sort(order.begin(), order.end(), [&](int left, int right) {
        return state.components[left].triangleStart < state.components[right].triangleStart;
    });
    std::ostringstream json;
    json << "{\"schema\":\"design-studio.semantic-assembly/2\","
         << "\"source_format\":\"AP242\",\"source_step_sha256\":\""
         << jsonEscape(sourceSha) << "\",\"tessellation_sha256\":\""
         << jsonEscape(tessellationSha) << "\",\"identity_available\":true,"
         << "\"legacy_flattened\":false,\"components\":[";
    for (std::size_t position = 0; position < order.size(); ++position) {
        if (position) json << ',';
        const ComponentState& component = state.components[order[position]];
        json << "{\"semantic_id\":\"" << jsonEscape(component.semanticId)
             << "\",\"parent_id\":\"" << jsonEscape(component.parentId)
             << "\",\"name\":\"" << jsonEscape(component.name)
             << "\",\"reference_designator\":\""
             << jsonEscape(component.referenceDesignator)
             << "\",\"material\":\"" << jsonEscape(component.material)
             << "\",\"color_rgba\":[" << std::setprecision(17)
             << component.color[0] << ',' << component.color[1] << ','
             << component.color[2] << ',' << component.color[3] << "],\"placement\":[";
        for (int row = 1; row <= 4; ++row) {
            for (int column = 1; column <= 4; ++column) {
                if (row != 1 || column != 1) json << ',';
                // gp_Trsf exposes only the affine 3x4 portion.  The final
                // homogeneous row is implicit and must not call Value(4,*),
                // which raises an OCCT exception.
                json << ((row == 4) ? (column == 4 ? 1.0 : 0.0)
                                     : component.placement.Value(row, column));
            }
        }
        json << "],\"triangle_start\":" << component.triangleStart
             << ",\"triangle_count\":" << component.triangleCount
             << ",\"face_ids\":[";
        int faceIndex = 0;
        for (TopExp_Explorer explorer(component.shape, TopAbs_FACE);
             explorer.More(); explorer.Next()) {
            if (faceIndex++) json << ',';
            json << '"' << jsonEscape(component.semanticId + ":Face"
                                       + std::to_string(faceIndex)) << '"';
        }
        json << "]}";
    }
    json << "]}";
    state.semanticJson = json.str();
    return true;
}

} // namespace

DC_API std::int32_t dc_brep_load_step(const char* path, DcShapeHandle* out)
{
    if (!path || !out) return DC_ERR_INVALID_ARG;
    *out = nullptr;
    try {
        STEPControl_Reader reader;
        if (reader.ReadFile(path) != IFSelect_RetDone) return DC_ERR_IO;
        if (reader.NbRootsForTransfer() <= 0 || reader.TransferRoots() <= 0)
            return DC_ERR_PARSE;
        TopoDS_Shape shape = reader.OneShape();
        if (shape.IsNull() || !BRepCheck_Analyzer(shape).IsValid())
            return DC_ERR_GEOMETRY;
        auto* state = new (std::nothrow) ShapeState;
        if (!state) return DC_ERR_GEOMETRY;
        state->shape = std::move(shape);
        // STEPControl remains the geometry authority for every supported STEP
        // flavor.  XCAF is an optional identity/appearance pass; failure here
        // deliberately leaves a valid legacy flattened viewer instead of
        // fabricating component ownership.
        loadXcafComponents(path, *state);
        *out = state;
        return DC_OK;
    } catch (...) {
        return DC_ERR_GEOMETRY;
    }
}

DC_API std::int32_t dc_brep_tessellate(DcShapeHandle handle,
                                       double deflectionMm,
                                       DcMesh* out)
{
    if (!handle) return DC_ERR_INVALID_HANDLE;
    if (!out || !(deflectionMm > 0.0) || deflectionMm > 100.0)
        return DC_ERR_INVALID_ARG;
    *out = DcMesh{0, 0, nullptr, nullptr, nullptr};
    auto& state = *static_cast<ShapeState*>(handle);
    try {
        BRepMesh_IncrementalMesh mesher(state.shape, deflectionMm, false, 0.35, true);
        if (!mesher.IsDone()) return DC_ERR_GEOMETRY;
        state.vertices.clear();
        state.indices.clear();
        state.triangleOwners.clear();

        constexpr std::int64_t MaxVertices = 10'000'000;
        constexpr std::int64_t MaxTriangles = 10'000'000;
        for (TopExp_Explorer explorer(state.shape, TopAbs_FACE); explorer.More(); explorer.Next()) {
            const TopoDS_Face face = TopoDS::Face(explorer.Current());
            TopLoc_Location location;
            const Handle(Poly_Triangulation) triangles = BRep_Tool::Triangulation(face, location);
            if (triangles.IsNull()) continue;
            const std::int64_t base = static_cast<std::int64_t>(state.vertices.size() / 3);
            if (base + triangles->NbNodes() > MaxVertices
                || static_cast<std::int64_t>(state.indices.size() / 3)
                       + triangles->NbTriangles() > MaxTriangles) {
                state.vertices.clear(); state.indices.clear();
                return DC_ERR_GEOMETRY;
            }
            const gp_Trsf transform = location.Transformation();
            for (int node = 1; node <= triangles->NbNodes(); ++node) {
                const gp_Pnt point = triangles->Node(node).Transformed(transform);
                if (!safeFloat(point.X()) || !safeFloat(point.Y()) || !safeFloat(point.Z()))
                    return DC_ERR_GEOMETRY;
                state.vertices.push_back(static_cast<float>(point.X()));
                state.vertices.push_back(static_cast<float>(point.Y()));
                state.vertices.push_back(static_cast<float>(point.Z()));
            }
            const int faceOwner = componentForFace(state, face);
            for (int triangle = 1; triangle <= triangles->NbTriangles(); ++triangle) {
                int a = 0, b = 0, c = 0;
                triangles->Triangle(triangle).Get(a, b, c);
                const gp_Pnt pa = triangles->Node(a).Transformed(transform);
                const gp_Pnt pb = triangles->Node(b).Transformed(transform);
                const gp_Pnt pc = triangles->Node(c).Transformed(transform);
                if (gp_Vec(pa, pb).Crossed(gp_Vec(pa, pc)).SquareMagnitude() <= 1.0e-18)
                    continue;
                if (face.Orientation() == TopAbs_REVERSED) std::swap(b, c);
                state.indices.push_back(static_cast<std::int32_t>(base + a - 1));
                state.indices.push_back(static_cast<std::int32_t>(base + b - 1));
                state.indices.push_back(static_cast<std::int32_t>(base + c - 1));
                state.triangleOwners.push_back(faceOwner);
            }
        }
        if (state.vertices.empty() || state.indices.empty()) return DC_ERR_GEOMETRY;
        out->nverts = static_cast<std::int64_t>(state.vertices.size() / 3);
        out->ntris = static_cast<std::int64_t>(state.indices.size() / 3);
        out->xyz = state.vertices.data();
        out->nrm = nullptr;
        out->idx = state.indices.data();
        return DC_OK;
    } catch (...) {
        state.vertices.clear(); state.indices.clear();
        return DC_ERR_GEOMETRY;
    }
}

DC_API std::int32_t dc_brep_semantic_assembly(DcShapeHandle handle,
                                               const char* sourceStepSha256,
                                               const char* tessellationSha256,
                                               DcSemanticAssemblyJson* out)
{
    if (!handle) return DC_ERR_INVALID_HANDLE;
    if (!out || !sourceStepSha256 || !tessellationSha256)
        return DC_ERR_INVALID_ARG;
    *out = DcSemanticAssemblyJson{0, nullptr};
    auto& state = *static_cast<ShapeState*>(handle);
    try {
        if (!buildSemanticJson(state, sourceStepSha256, tessellationSha256))
            return DC_ERR_UNSUPPORTED;
        out->size = static_cast<std::int64_t>(state.semanticJson.size());
        out->json = state.semanticJson.c_str();
        return DC_OK;
    } catch (const Standard_Failure&) {
        state.semanticJson.clear();
        return DC_ERR_GEOMETRY;
    } catch (const std::exception&) {
        state.semanticJson.clear();
        return DC_ERR_GEOMETRY;
    } catch (...) {
        state.semanticJson.clear();
        return DC_ERR_GEOMETRY;
    }
}

DC_API std::int32_t dc_assembly_flatten(DcDocHandle, DcMesh* out)
{
    if (out) *out = DcMesh{0, 0, nullptr, nullptr, nullptr};
    return DC_ERR_UNSUPPORTED;
}

DC_API void dc_shape_destroy(DcShapeHandle handle)
{
    delete static_cast<ShapeState*>(handle);
}

#endif // HAS_OCCT
