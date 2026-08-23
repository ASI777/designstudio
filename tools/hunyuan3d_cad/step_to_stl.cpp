#include <BRepBndLib.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <Bnd_Box.hxx>
#include <IFSelect_ReturnStatus.hxx>
#include <STEPControl_Reader.hxx>
#include <ShapeFix_Shape.hxx>
#include <StlAPI_Writer.hxx>
#include <TopoDS_Shape.hxx>

#include <algorithm>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>

int main(int argc, char** argv) {
    if (argc < 3 || argc > 4) {
        std::cerr << "usage: step_to_stl INPUT.step OUTPUT.stl [linear_deflection_mm]\n";
        return 2;
    }

    const std::string input = argv[1];
    const std::string output = argv[2];
    const double deflection = argc == 4 ? std::stod(argv[3]) : 0.02;
    if (!(deflection > 0.0)) {
        std::cerr << "linear deflection must be positive\n";
        return 2;
    }

    try {
        STEPControl_Reader reader;
        if (reader.ReadFile(input.c_str()) != IFSelect_RetDone) {
            throw std::runtime_error("Open CASCADE could not read the STEP file");
        }
        if (reader.TransferRoots() <= 0) {
            throw std::runtime_error("STEP file contains no transferable roots");
        }

        ShapeFix_Shape fixer(reader.OneShape());
        fixer.Perform();
        TopoDS_Shape shape = fixer.Shape();
        if (shape.IsNull()) {
            throw std::runtime_error("STEP transfer produced a null shape");
        }

        Bnd_Box box;
        BRepBndLib::AddOptimal(shape, box);
        if (box.IsVoid()) {
            throw std::runtime_error("STEP shape has no finite bounding box");
        }
        double xmin, ymin, zmin, xmax, ymax, zmax;
        box.Get(xmin, ymin, zmin, xmax, ymax, zmax);

        BRepMesh_IncrementalMesh mesher(shape, deflection, false, 0.35, true);
        mesher.Perform();
        if (!mesher.IsDone()) {
            throw std::runtime_error("Open CASCADE tessellation failed");
        }

        StlAPI_Writer writer;
        if (!writer.Write(shape, output.c_str())) {
            throw std::runtime_error("failed to write STL");
        }

        std::cout << std::fixed << std::setprecision(6)
                  << "{\"bbox_min_mm\":[" << xmin << "," << ymin << "," << zmin
                  << "],\"bbox_max_mm\":[" << xmax << "," << ymax << "," << zmax
                  << "],\"dimensions_mm\":[" << xmax - xmin << "," << ymax - ymin
                  << "," << zmax - zmin << "]}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "step_to_stl: " << error.what() << "\n";
        return 1;
    }
}
