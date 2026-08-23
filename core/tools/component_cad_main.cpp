#include <BRepBndLib.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepPrimAPI_MakeCylinder.hxx>
#include <BRepPrimAPI_MakeSphere.hxx>
#include <BRep_Builder.hxx>
#include <Bnd_Box.hxx>
#include <STEPControl_Reader.hxx>
#include <STEPControl_Writer.hxx>
#include <STEPCAFControl_Writer.hxx>
#include <Interface_Static.hxx>
#include <Standard_Failure.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS_Compound.hxx>
#include <TopoDS_Shape.hxx>
#include <TDataStd_Name.hxx>
#include <TCollection_HAsciiString.hxx>
#include <TDocStd_Document.hxx>
#include <XCAFDoc_ColorTool.hxx>
#include <XCAFDoc_DocumentTool.hxx>
#include <XCAFDoc_MaterialTool.hxx>
#include <XCAFDoc_ShapeTool.hxx>
#include <Quantity_Color.hxx>
#include <TCollection_ExtendedString.hxx>
#include <gp_Ax1.hxx>
#include <gp_Ax2.hxx>
#include <gp_Dir.hxx>
#include <gp_Pnt.hxx>
#include <gp_Trsf.hxx>
#include <gp_Vec.hxx>

#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace {

struct Primitive {
    std::string kind;
    std::string id;
    double cx{}, cy{}, cz{}, sx{}, sy{}, sz{}, rx{}, ry{}, rz{};
};

TopoDS_Shape transform(TopoDS_Shape shape, const Primitive& primitive)
{
    const struct AxisRotation { gp_Dir axis; double degrees; } rotations[] = {
        {gp_Dir(1, 0, 0), primitive.rx}, {gp_Dir(0, 1, 0), primitive.ry},
        {gp_Dir(0, 0, 1), primitive.rz},
    };
    for (const auto& rotation : rotations) {
        if (std::abs(rotation.degrees) < 1e-12) continue;
        gp_Trsf value;
        value.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), rotation.axis),
                          rotation.degrees * M_PI / 180.0);
        shape = BRepBuilderAPI_Transform(shape, value, true).Shape();
    }
    gp_Trsf translation;
    translation.SetTranslation(gp_Vec(primitive.cx, primitive.cy, primitive.cz));
    return BRepBuilderAPI_Transform(shape, translation, true).Shape();
}

TopoDS_Shape build(const Primitive& primitive)
{
    if (!(primitive.sx > 0 && primitive.sy > 0 && primitive.sz > 0))
        throw std::runtime_error(primitive.id + " has non-positive dimensions");
    TopoDS_Shape shape;
    if (primitive.kind == "box") {
        shape = BRepPrimAPI_MakeBox(gp_Pnt(-primitive.sx / 2, -primitive.sy / 2,
                                           -primitive.sz / 2),
                                    primitive.sx, primitive.sy, primitive.sz).Shape();
    } else if (primitive.kind == "cylinder") {
        if (std::abs(primitive.sx - primitive.sy) > 1e-6)
            throw std::runtime_error(primitive.id + " requires equal cylinder X/Y diameters");
        shape = BRepPrimAPI_MakeCylinder(
            gp_Ax2(gp_Pnt(0, 0, -primitive.sz / 2), gp_Dir(0, 0, 1)),
            primitive.sx / 2, primitive.sz).Shape();
    } else if (primitive.kind == "dome") {
        if (std::abs(primitive.sx - primitive.sy) > 1e-6
            || std::abs(primitive.sx - primitive.sz * 2) > 1e-6)
            throw std::runtime_error(primitive.id + " dome must be a grounded hemisphere");
        shape = BRepPrimAPI_MakeSphere(gp_Pnt(0, 0, -primitive.sz / 2),
                                       primitive.sx / 2, 0.0, M_PI / 2).Shape();
    } else {
        throw std::runtime_error("unsupported primitive operation: " + primitive.kind);
    }
    shape = transform(shape, primitive);
    if (shape.IsNull() || !BRepCheck_Analyzer(shape).IsValid())
        throw std::runtime_error(primitive.id + " produced an invalid B-Rep");
    bool solid = false;
    for (TopExp_Explorer it(shape, TopAbs_SOLID); it.More(); it.Next()) solid = true;
    if (!solid) throw std::runtime_error(primitive.id + " did not produce a solid");
    return shape;
}

int solidCount(const TopoDS_Shape& shape)
{
    int count = 0;
    for (TopExp_Explorer it(shape, TopAbs_SOLID); it.More(); it.Next()) ++count;
    return count;
}

std::vector<Primitive> parse(const fs::path& path)
{
    std::ifstream input(path);
    if (!input) throw std::runtime_error("cannot read CAD command stream");
    std::vector<Primitive> result;
    std::string line;
    int lineNumber = 0;
    while (std::getline(input, line)) {
        ++lineNumber;
        if (line.empty() || line[0] == '#') continue;
        std::istringstream row(line);
        Primitive primitive;
        if (!(row >> primitive.kind >> primitive.id >> primitive.cx >> primitive.cy >> primitive.cz
                  >> primitive.sx >> primitive.sy >> primitive.sz
                  >> primitive.rx >> primitive.ry >> primitive.rz))
            throw std::runtime_error("invalid CAD command at line " + std::to_string(lineNumber));
        std::string extra;
        if (row >> extra) throw std::runtime_error("unexpected CAD command data at line "
                                                   + std::to_string(lineNumber));
        result.push_back(primitive);
    }
    if (result.empty()) throw std::runtime_error("CAD command stream contains no solids");
    return result;
}

void writeStep(const std::vector<TopoDS_Shape>& shapes,
               const std::vector<Primitive>& primitives, const fs::path& destination)
{
    fs::create_directories(destination.parent_path());
    const fs::path temporary = destination.string() + ".tmp";
    Handle(TDocStd_Document) document = new TDocStd_Document("DesignStudioAP242");
    const Handle(XCAFDoc_ShapeTool) shapeTool = XCAFDoc_DocumentTool::ShapeTool(document->Main());
    const Handle(XCAFDoc_ColorTool) colorTool = XCAFDoc_DocumentTool::ColorTool(document->Main());
    const Handle(XCAFDoc_MaterialTool) materialTool = XCAFDoc_DocumentTool::MaterialTool(document->Main());
    if (shapeTool.IsNull() || colorTool.IsNull() || materialTool.IsNull())
        throw std::runtime_error("Open CASCADE could not create the XCAF assembly tools");
    for (std::size_t index = 0; index < shapes.size(); ++index) {
        const TDF_Label label = shapeTool->AddShape(shapes[index], Standard_False);
        TDataStd_Name::Set(label, TCollection_ExtendedString(primitives[index].id.c_str()));
        const Quantity_Color colors[] = {
            Quantity_Color(0.85, 0.20, 0.05, Quantity_TOC_RGB),
            Quantity_Color(0.05, 0.25, 0.85, Quantity_TOC_RGB),
            Quantity_Color(0.10, 0.65, 0.25, Quantity_TOC_RGB),
            Quantity_Color(0.70, 0.20, 0.75, Quantity_TOC_RGB),
        };
        colorTool->SetColor(label, colors[index % 4], XCAFDoc_ColorGen);
        const std::string material = primitives[index].id == "U1" ? "silicon" : "FR4";
        const double density = material == "silicon" ? 2.33 : 1.85;
        materialTool->SetMaterial(
            label,
            new TCollection_HAsciiString(material.c_str()),
            new TCollection_HAsciiString("DesignStudio AP242 fixture material"),
            density,
            new TCollection_HAsciiString("g/cm3"),
            new TCollection_HAsciiString("mass density"));
    }
    STEPCAFControl_Writer writer;
    writer.SetColorMode(Standard_True);
    writer.SetNameMode(Standard_True);
    // STEPControl_Writer initializes the STEP session and its static defaults in
    // its constructor, so select AP242 only after the writer exists.
    if (!Interface_Static::SetIVal("write.step.schema", 5)
        || Interface_Static::IVal("write.step.schema") != 5)
        throw std::runtime_error("Open CASCADE does not support AP242 STEP export");
    if (!writer.Transfer(document, STEPControl_AsIs))
        throw std::runtime_error("Open CASCADE could not transfer the XCAF assembly");
    if (writer.Write(temporary.string().c_str()) != IFSelect_RetDone)
        throw std::runtime_error("Open CASCADE could not write STEP");
    STEPControl_Reader reader;
    if (reader.ReadFile(temporary.string().c_str()) != IFSelect_RetDone
        || reader.TransferRoots() <= 0)
        throw std::runtime_error("generated STEP failed round-trip import");
    const TopoDS_Shape reloaded = reader.OneShape();
    if (reloaded.IsNull() || !BRepCheck_Analyzer(reloaded).IsValid()
        || solidCount(reloaded) != static_cast<int>(shapes.size()))
        throw std::runtime_error("generated STEP changed solid count during round trip");
    std::error_code error;
    fs::remove(destination, error);
    error.clear();
    fs::rename(temporary, destination, error);
    if (error) throw std::runtime_error("cannot commit STEP artifact: " + error.message());
}

} // namespace

int main(int argc, char** argv)
{
    if (argc != 3) {
        std::cerr << "usage: designstudio-component-cad COMMANDS.txt OUTPUT.step\n";
        return 2;
    }
    try {
        const auto primitives = parse(argv[1]);
        std::vector<TopoDS_Shape> shapes;
        shapes.reserve(primitives.size());
        Bnd_Box bounds;
        for (const auto& primitive : primitives) {
            shapes.push_back(build(primitive));
            BRepBndLib::Add(shapes.back(), bounds);
        }
        writeStep(shapes, primitives, argv[2]);
        double x0, y0, z0, x1, y1, z1;
        bounds.Get(x0, y0, z0, x1, y1, z1);
        std::cout << std::setprecision(12)
                  << "{\"ok\":true,\"step_schema\":\"AP242\",\"solid_count\":"
                  << shapes.size()
                  << ",\"bounds_mm\":[" << x0 << ',' << y0 << ',' << z0 << ','
                  << x1 << ',' << y1 << ',' << z1 << "]}\n";
        return 0;
    } catch (const Standard_Failure& error) {
        std::cerr << "Open CASCADE failure: " << error.GetMessageString() << '\n';
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
    }
    return 1;
}
