#include <BRepAlgoAPI_Cut.hxx>
#include <BRepBuilderAPI_GTransform.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepPrimAPI_MakeCylinder.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <Interface_Static.hxx>
#include <STEPControl_Reader.hxx>
#include <STEPControl_Writer.hxx>
#include <STEPCAFControl_Writer.hxx>
#include <Standard_Failure.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Shape.hxx>
#include <TDataStd_Name.hxx>
#include <TDocStd_Document.hxx>
#include <XCAFDoc_DocumentTool.hxx>
#include <XCAFDoc_ShapeTool.hxx>
#include <gp_GTrsf.hxx>
#include <gp_Mat.hxx>
#include <gp_Pnt.hxx>
#include <gp_Vec.hxx>
#include <gp_XYZ.hxx>

#include <array>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace {

using Point = std::array<double, 2>;

struct Hole {
    double x{};
    double y{};
    double diameter{};
};

struct Component {
    std::string id;
    fs::path path;
    std::array<double, 12> transform{};
};

struct Job {
    double thickness{};
    std::vector<Point> outline;
    std::vector<std::vector<Point>> cutouts;
    std::vector<Hole> holes;
    std::vector<Component> components;
};

std::vector<Point> readPolygon(std::istringstream& row, int count, int lineNumber)
{
    if (count < 3) throw std::runtime_error("polygon has fewer than three points at line "
                                            + std::to_string(lineNumber));
    std::vector<Point> result(static_cast<std::size_t>(count));
    for (Point& point : result)
        if (!(row >> point[0] >> point[1]))
            throw std::runtime_error("incomplete polygon at line " + std::to_string(lineNumber));
    return result;
}

Job readJob(const fs::path& path)
{
    std::ifstream input(path);
    if (!input) throw std::runtime_error("cannot read board STEP job");
    Job job;
    std::string line;
    int lineNumber = 0;
    bool haveBoard = false;
    while (std::getline(input, line)) {
        ++lineNumber;
        if (line.empty() || line[0] == '#') continue;
        std::istringstream row(line);
        std::string command;
        row >> command;
        if (command == "BOARD") {
            int count = 0;
            if (haveBoard || !(row >> job.thickness >> count) || !(job.thickness > 0))
                throw std::runtime_error("invalid BOARD command at line "
                                         + std::to_string(lineNumber));
            job.outline = readPolygon(row, count, lineNumber);
            haveBoard = true;
        } else if (command == "CUTOUT") {
            int count = 0;
            if (!(row >> count))
                throw std::runtime_error("invalid CUTOUT command at line "
                                         + std::to_string(lineNumber));
            job.cutouts.push_back(readPolygon(row, count, lineNumber));
        } else if (command == "HOLE") {
            Hole hole;
            if (!(row >> hole.x >> hole.y >> hole.diameter) || !(hole.diameter > 0))
                throw std::runtime_error("invalid HOLE command at line "
                                         + std::to_string(lineNumber));
            job.holes.push_back(hole);
        } else if (command == "COMPONENT") {
            Component component;
            std::string pathValue;
            if (!(row >> std::quoted(component.id) >> std::quoted(pathValue)))
                throw std::runtime_error("invalid COMPONENT command at line "
                                         + std::to_string(lineNumber));
            component.path = pathValue;
            for (double& value : component.transform)
                if (!(row >> value))
                    throw std::runtime_error("incomplete COMPONENT transform at line "
                                             + std::to_string(lineNumber));
            if (!fs::is_regular_file(component.path))
                throw std::runtime_error(component.id + ": STEP asset is missing");
            job.components.push_back(std::move(component));
        } else {
            throw std::runtime_error("unknown command at line " + std::to_string(lineNumber));
        }
        std::string extra;
        if (row >> extra)
            throw std::runtime_error("unexpected data at line " + std::to_string(lineNumber));
    }
    if (!haveBoard) throw std::runtime_error("board STEP job has no BOARD command");
    return job;
}

TopoDS_Face makeFace(const std::vector<Point>& points)
{
    BRepBuilderAPI_MakePolygon polygon;
    for (const Point& point : points) polygon.Add(gp_Pnt(point[0], point[1], 0));
    polygon.Close();
    if (!polygon.IsDone()) throw std::runtime_error("cannot construct board polygon");
    BRepBuilderAPI_MakeFace face(polygon.Wire());
    if (!face.IsDone()) throw std::runtime_error("cannot construct board face");
    return face.Face();
}

TopoDS_Shape makeBoard(const Job& job)
{
    TopoDS_Shape board = BRepPrimAPI_MakePrism(makeFace(job.outline),
                                                gp_Vec(0, 0, job.thickness)).Shape();
    for (const auto& points : job.cutouts) {
        TopoDS_Shape cutter = BRepPrimAPI_MakePrism(makeFace(points),
                                                    gp_Vec(0, 0, job.thickness + 0.2)).Shape();
        gp_GTrsf shift;
        shift.SetTranslationPart(gp_XYZ(0, 0, -0.1));
        cutter = BRepBuilderAPI_GTransform(cutter, shift, true).Shape();
        board = BRepAlgoAPI_Cut(board, cutter).Shape();
    }
    for (const Hole& hole : job.holes) {
        TopoDS_Shape cutter = BRepPrimAPI_MakeCylinder(
            gp_Ax2(gp_Pnt(hole.x, hole.y, -0.1), gp_Dir(0, 0, 1)),
            hole.diameter / 2, job.thickness + 0.2).Shape();
        board = BRepAlgoAPI_Cut(board, cutter).Shape();
    }
    if (board.IsNull() || !BRepCheck_Analyzer(board).IsValid())
        throw std::runtime_error("board substrate is not a valid B-Rep after drilling");
    return board;
}

TopoDS_Shape loadComponent(const Component& component)
{
    STEPControl_Reader reader;
    if (reader.ReadFile(component.path.string().c_str()) != IFSelect_RetDone
        || reader.TransferRoots() <= 0)
        throw std::runtime_error(component.id + ": cannot import STEP asset");
    TopoDS_Shape shape = reader.OneShape();
    if (shape.IsNull()) throw std::runtime_error(component.id + ": STEP asset has no shape");
    const auto& m = component.transform;
    gp_GTrsf placement;
    placement.SetVectorialPart(gp_Mat(m[0], m[1], m[2],
                                     m[4], m[5], m[6],
                                     m[8], m[9], m[10]));
    placement.SetTranslationPart(gp_XYZ(m[3], m[7], m[11]));
    shape = BRepBuilderAPI_GTransform(shape, placement, true).Shape();
    if (shape.IsNull() || !BRepCheck_Analyzer(shape).IsValid())
        throw std::runtime_error(component.id + ": transformed STEP asset is invalid");
    return shape;
}

int solidCount(const TopoDS_Shape& shape)
{
    int count = 0;
    for (TopExp_Explorer it(shape, TopAbs_SOLID); it.More(); it.Next()) ++count;
    return count;
}

void writeStep(const Job& job, const std::vector<TopoDS_Shape>& shapes,
               const fs::path& destination)
{
    if (destination.has_parent_path()) fs::create_directories(destination.parent_path());
    const fs::path temporary = destination.string() + ".tmp";
    // Keep every board/member as a named XCAF product.  STEPControl_Writer
    // emits valid AP242 geometry but flattens the assembly, which prevents
    // the DesignStudio viewer from identifying or isolating component bodies.
    // STEPCAFControl_Writer preserves the engineering assembly identity while
    // retaining the same B-Rep validation below.
    Handle(TDocStd_Document) document = new TDocStd_Document("DesignStudioAP242");
    const Handle(XCAFDoc_ShapeTool) shapeTool =
        XCAFDoc_DocumentTool::ShapeTool(document->Main());
    if (shapeTool.IsNull()) throw std::runtime_error("cannot create XCAF shape tool");
    const std::vector<std::string> names = [&] {
        std::vector<std::string> values;
        values.reserve(shapes.size());
        values.emplace_back("PCB_BOARD");
        for (const Component& component : job.components) values.push_back(component.id);
        return values;
    }();
    for (std::size_t index = 0; index < shapes.size(); ++index) {
        const TDF_Label label = shapeTool->AddShape(shapes[index], Standard_False);
        TDataStd_Name::Set(label, TCollection_ExtendedString(names.at(index).c_str()));
    }
    STEPCAFControl_Writer writer;
    writer.SetColorMode(Standard_True);
    writer.SetNameMode(Standard_True);
    writer.SetLayerMode(Standard_True);
    writer.SetPropsMode(Standard_True);
    if (!Interface_Static::SetIVal("write.step.schema", 5)
        || Interface_Static::IVal("write.step.schema") != 5)
        throw std::runtime_error("Open CASCADE does not support AP242 STEP export");
    int expectedSolids = 0;
    for (const TopoDS_Shape& shape : shapes) {
        expectedSolids += solidCount(shape);
    }
    if (!writer.Transfer(document, STEPControl_AsIs))
        throw std::runtime_error("Open CASCADE could not transfer XCAF board assembly");
    if (writer.Write(temporary.string().c_str()) != IFSelect_RetDone)
        throw std::runtime_error("Open CASCADE could not write board STEP");
    STEPControl_Reader reader;
    if (reader.ReadFile(temporary.string().c_str()) != IFSelect_RetDone
        || reader.TransferRoots() <= 0)
        throw std::runtime_error("board STEP failed round-trip import");
    const TopoDS_Shape reloaded = reader.OneShape();
    const int actualSolids = solidCount(reloaded);
    if (reloaded.IsNull() || !BRepCheck_Analyzer(reloaded).IsValid()
        || actualSolids != expectedSolids)
        throw std::runtime_error("board STEP changed solid count during round trip");
    std::error_code error;
    fs::remove(destination, error);
    error.clear();
    fs::rename(temporary, destination, error);
    if (error) throw std::runtime_error("cannot commit board STEP: " + error.message());
}

} // namespace

int main(int argc, char** argv)
{
    if (argc != 3) {
        std::cerr << "usage: designstudio-board-step BOARD_JOB.txt OUTPUT.step\n";
        return 2;
    }
    try {
        const Job job = readJob(argv[1]);
        std::vector<TopoDS_Shape> shapes;
        shapes.reserve(job.components.size() + 1);
        shapes.push_back(makeBoard(job));
        for (const Component& component : job.components)
            shapes.push_back(loadComponent(component));
        writeStep(job, shapes, argv[2]);
        int solids = 0;
        for (const auto& shape : shapes) solids += solidCount(shape);
        std::cout << "{\"ok\":true,\"step_schema\":\"AP242\",\"components\":"
                  << job.components.size() << ",\"holes\":" << job.holes.size()
                  << ",\"solids\":" << solids << "}\n";
        return 0;
    } catch (const Standard_Failure& error) {
        std::cerr << "Open CASCADE failure: " << error.GetMessageString() << '\n';
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
    }
    return 1;
}
