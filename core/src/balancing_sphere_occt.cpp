#include "designcore/balancing_sphere.h"

#ifdef HAS_OCCT

#include <BRepAlgoAPI_Common.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepBndLib.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepPrimAPI_MakeCylinder.hxx>
#include <BRepPrimAPI_MakeSphere.hxx>
#include <BRepPrimAPI_MakeTorus.hxx>
#include <BRep_Builder.hxx>
#include <Bnd_Box.hxx>
#include <STEPControl_Reader.hxx>
#include <STEPControl_Writer.hxx>
#include <Standard_Failure.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS_Compound.hxx>
#include <TopoDS_Shape.hxx>
#include <gp_Ax2.hxx>
#include <gp_Dir.hxx>
#include <gp_Pnt.hxx>
#include <gp_Vec.hxx>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <string>
#include <system_error>
#include <vector>

namespace dc::mechatronics {
namespace {

namespace fs = std::filesystem;

struct Part {
    std::string id;
    std::string domain;
    std::string role;
    std::string material;
    double baseMassKg = 0.0;
    double massKg = 0.0;
    TopoDS_Shape shape;
};

TopoDS_Shape centeredBox(double sx, double sy, double sz,
                         double cx, double cy, double cz)
{
    return BRepPrimAPI_MakeBox(gp_Pnt(cx - sx * 0.5, cy - sy * 0.5, cz - sz * 0.5),
                               sx, sy, sz).Shape();
}

TopoDS_Shape cylinderBetween(const gp_Pnt& from, const gp_Pnt& to, double radius)
{
    const gp_Vec vector(from, to);
    return BRepPrimAPI_MakeCylinder(gp_Ax2(from, gp_Dir(vector)), radius,
                                    vector.Magnitude()).Shape();
}

bool validSolidShape(const TopoDS_Shape& shape)
{
    if (shape.IsNull() || !BRepCheck_Analyzer(shape).IsValid()) return false;
    for (TopExp_Explorer explorer(shape, TopAbs_SOLID); explorer.More(); explorer.Next())
        return true;
    return false;
}

int solidCount(const TopoDS_Shape& shape)
{
    int count = 0;
    for (TopExp_Explorer explorer(shape, TopAbs_SOLID); explorer.More(); explorer.Next()) ++count;
    return count;
}

TopoDS_Compound compoundOf(const std::vector<Part>& parts, bool omitUpperShell)
{
    BRep_Builder builder;
    TopoDS_Compound compound;
    builder.MakeCompound(compound);
    for (const Part& part : parts) {
        if (omitUpperShell && part.id == "shell_upper") continue;
        builder.Add(compound, part.shape);
    }
    return compound;
}

bool writeAndRoundTripStep(const std::vector<TopoDS_Shape>& shapes,
                           const fs::path& destination,
                           std::string& error)
{
    const fs::path temporary = destination.string() + ".tmp";
    STEPControl_Writer writer;
    for (const TopoDS_Shape& shape : shapes) {
        if (writer.Transfer(shape, STEPControl_AsIs) != IFSelect_RetDone) {
            error = "Open CASCADE could not transfer shape to STEP: " + destination.string();
            return false;
        }
    }
    if (writer.Write(temporary.string().c_str()) != IFSelect_RetDone) {
        error = "Open CASCADE could not write STEP: " + destination.string();
        return false;
    }
    STEPControl_Reader reader;
    if (reader.ReadFile(temporary.string().c_str()) != IFSelect_RetDone
        || reader.NbRootsForTransfer() <= 0 || reader.TransferRoots() <= 0) {
        fs::remove(temporary);
        error = "STEP round-trip validation failed: " + destination.string();
        return false;
    }
    const TopoDS_Shape reloaded = reader.OneShape();
    if (reloaded.IsNull() || !BRepCheck_Analyzer(reloaded).IsValid()
        || solidCount(reloaded) <= 0) {
        fs::remove(temporary);
        error = "STEP round-trip produced invalid or empty solids: " + destination.string();
        return false;
    }
    std::error_code ec;
    fs::remove(destination, ec);
    ec.clear();
    fs::rename(temporary, destination, ec);
    if (ec) {
        fs::remove(temporary);
        error = "cannot commit STEP artifact: " + ec.message();
        return false;
    }
    return true;
}

std::string isoTime()
{
    const auto now = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    std::tm utc{};
#ifdef _WIN32
    gmtime_s(&utc, &now);
#else
    gmtime_r(&now, &utc);
#endif
    std::ostringstream stream;
    stream << std::put_time(&utc, "%Y-%m-%dT%H:%M:%SZ");
    return stream.str();
}

bool writeProject(const fs::path& path,
                  const BalancingSphereConfig& config,
                  const BalanceCommand& command,
                  const std::vector<Part>& parts,
                  std::string& error)
{
    const fs::path temporary = path.string() + ".tmp";
    std::ofstream out(temporary, std::ios::binary | std::ios::trunc);
    if (!out) { error = "cannot create mechatronic project"; return false; }
    out << std::setprecision(10);
    out << "{\n"
        << "  \"format\": \"design-studio.project/3\",\n"
        << "  \"units\": \"nm\",\n"
        << "  \"materials\": [\n"
        << "    {\"id\":\"pc_shell\",\"mech\":{\"E_GPa\":2.3,\"nu\":0.37,\"yield_MPa\":65,\"density_kg_m3\":1200}},\n"
        << "    {\"id\":\"al_6061_t6\",\"mech\":{\"E_GPa\":68.9,\"nu\":0.33,\"yield_MPa\":276,\"density_kg_m3\":2700}},\n"
        << "    {\"id\":\"fr4\",\"mech\":{\"E_GPa\":22,\"nu\":0.15,\"density_kg_m3\":1850},\"electronic\":{\"er\":4.2,\"tan_delta\":0.018}},\n"
        << "    {\"id\":\"tungsten_ballast\",\"mech\":{\"E_GPa\":400,\"nu\":0.28,\"density_kg_m3\":19300}},\n"
        << "    {\"id\":\"generic_component\",\"mech\":{\"density_kg_m3\":2500}}\n"
        << "  ],\n  \"parts\": [\n";
    for (std::size_t i = 0; i < parts.size(); ++i) {
        const Part& part = parts[i];
        out << "    {\"id\":\"" << part.id << "\",\"kind\":\"brep\","
            << "\"shape\":\"parts/" << part.id << ".step\","
            << "\"material\":\"" << part.material << "\","
            << "\"xform\":[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],"
            << "\"domain\":\"" << part.domain << "\","
            << "\"role\":\"" << part.role << "\","
            << "\"mass_model\":\"lumped_engineering_input\","
            << "\"mass_kg\":" << part.massKg << '}';
        out << (i + 1 == parts.size() ? "\n" : ",\n");
    }
    out << "  ],\n"
        << "  \"board\": {\n"
        << "    \"version\":2,\"document_id\":\"self-balancing-sphere-controller\",\"revision\":1,\n"
        << "    \"status\":\"architecture_only_requires_schematic_and_pcb_review\",\n"
        << "    \"supply\":{\"battery\":\"3S Li-ion, 11.1 V nominal\",\"fuse_a\":5,\"rails_v\":[12,5,3.3]},\n"
        << "    \"components\":[\"6-axis IMU\",\"MCU\",\"dual motor driver\",\"buck regulators\",\"encoders\",\"battery monitor\",\"fuse\",\"power switch\"],\n"
        << "    \"nets\":[\"BATT+\",\"GND\",\"+5V\",\"+3V3\",\"I2C_SDA\",\"I2C_SCL\",\"MOTOR_X_A\",\"MOTOR_X_B\",\"MOTOR_Y_A\",\"MOTOR_Y_B\"],\n"
        << "    \"controller\":{\"kind\":\"two_axis_moving_ballast_pd\","
        << "\"surface_roll_deg\":" << config.surfaceRollDeg << ','
        << "\"surface_pitch_deg\":" << config.surfacePitchDeg << ','
        << "\"roll_rate_deg_s\":" << config.rollRateDegS << ','
        << "\"pitch_rate_deg_s\":" << config.pitchRateDegS << ','
        << "\"target_x_mm\":" << command.targetXmm << ','
        << "\"target_y_mm\":" << command.targetYmm << ','
        << "\"maximum_static_angle_deg\":" << command.maximumStaticAngleDeg << "}\n"
        << "  },\n"
        << "  \"assembly\":{\"tree\":[\"shell_upper\",\"shell_lower\",\"internal_frame\",\"xy_ballast_gantry\",\"control_electronics\",\"power_system\"],"
        << "\"structural_requirements\":{\"target_safety_factor\":" << config.targetSafetyFactor << ",\"status\":\"fea_required\"},"
        << "\"joints\":["
        << "{\"kind\":\"prismatic\",\"a\":\"rail_x\",\"b\":\"ballast\",\"axis\":[1,0,0],\"limits\":[-"
        << config.railTravelMm << ',' << config.railTravelMm << "]},"
        << "{\"kind\":\"prismatic\",\"a\":\"rail_y\",\"b\":\"ballast\",\"axis\":[0,1,0],\"limits\":[-"
        << config.railTravelMm << ',' << config.railTravelMm << "]}]},\n"
        << "  \"constraints\":["
        << "{\"kind\":\"keepout\",\"domains\":[\"mechanical\",\"electronic\"],\"refs\":[\"shell_upper\",\"shell_lower\",\"controller_pcb\"],\"params\":{\"clearance_mm\":" << config.assemblyClearanceMm << "}},"
        << "{\"kind\":\"alignment\",\"domains\":[\"mechanical\",\"electronic\"],\"refs\":[\"imu\",\"rail_x\",\"rail_y\"],\"params\":{\"axis_error_max_deg\":0.5}}],\n"
        << "  \"tolerances\":[{\"scope\":\"shell_split\",\"gap_mm\":0.3},{\"scope\":\"ballast_rail\",\"running_clearance_mm\":0.2}],\n"
        << "  \"rationale\":[{\"ts\":\"" << isoTime() << "\","
        << "\"intent\":\"Demonstrate a spherical mechatronic assembly that shifts an internal ballast in response to surface angle and angular velocity.\","
        << "\"alternatives\":[\"reaction wheel\",\"control-moment gyro\",\"fluid ballast\"],"
        << "\"gate\":\"manual\",\"source\":\"human+deterministic-generator\","
        << "\"drove\":{\"mechanical\":[\"two-axis gantry\",\"split spherical shell\"],\"electronic\":[\"IMU\",\"motor drivers\",\"battery protection\"]}}]\n"
        << "}\n";
    out.close();
    if (!out) { fs::remove(temporary); error = "cannot finish mechatronic project"; return false; }
    std::error_code ec;
    fs::remove(path, ec); ec.clear(); fs::rename(temporary, path, ec);
    if (ec) { fs::remove(temporary); error = "cannot commit mechatronic project: " + ec.message(); return false; }
    return true;
}

bool writeControllerCsv(const fs::path& path,
                        const std::vector<BalanceSample>& samples,
                        std::string& error)
{
    const fs::path temporary = path.string() + ".tmp";
    std::ofstream out(temporary, std::ios::binary | std::ios::trunc);
    if (!out) { error = "cannot create controller response CSV"; return false; }
    out << "time_s,target_x_mm,target_y_mm,actual_x_mm,actual_y_mm,velocity_x_mm_s,velocity_y_mm_s\n";
    out << std::setprecision(10);
    for (const BalanceSample& sample : samples) {
        out << sample.timeS << ',' << sample.targetXmm << ',' << sample.targetYmm << ','
            << sample.state.xMm << ',' << sample.state.yMm << ','
            << sample.state.velocityXmmS << ',' << sample.state.velocityYmmS << '\n';
    }
    out.close();
    if (!out) { fs::remove(temporary); error = "cannot finish controller response CSV"; return false; }
    std::error_code ec;
    fs::remove(path, ec); ec.clear(); fs::rename(temporary, path, ec);
    if (ec) { fs::remove(temporary); error = "cannot commit controller CSV: " + ec.message(); return false; }
    return true;
}

std::vector<Part> buildParts(const BalancingSphereConfig& c,
                             const BalanceCommand& command)
{
    std::vector<Part> parts;
    const double r = c.outerRadiusMm;
    const double inner = r - c.wallThicknessMm;
    const TopoDS_Shape shell = BRepAlgoAPI_Cut(
        BRepPrimAPI_MakeSphere(r).Shape(), BRepPrimAPI_MakeSphere(inner).Shape()).Shape();
    const double clip = r + 2.0;
    const TopoDS_Shape upper = BRepAlgoAPI_Common(
        shell, BRepPrimAPI_MakeBox(gp_Pnt(-clip, -clip, 0.0),
                                   clip * 2.0, clip * 2.0, clip).Shape()).Shape();
    const TopoDS_Shape lower = BRepAlgoAPI_Common(
        shell, BRepPrimAPI_MakeBox(gp_Pnt(-clip, -clip, -clip),
                                   clip * 2.0, clip * 2.0, clip).Shape()).Shape();

    auto add = [&parts](std::string id, std::string domain, std::string role,
                        std::string material, double baseMass, TopoDS_Shape shape) {
        parts.push_back({std::move(id), std::move(domain), std::move(role),
                         std::move(material), baseMass, 0.0, std::move(shape)});
    };
    add("shell_upper", "mechanical", "removable_upper_hemisphere", "pc_shell", 0.115, upper);
    add("shell_lower", "mechanical", "load_bearing_lower_hemisphere", "pc_shell", 0.115, lower);
    add("equatorial_frame", "mechanical", "shell_reinforcement", "al_6061_t6", 0.060,
        BRepPrimAPI_MakeTorus(58.0, 2.5).Shape());
    const double railLength = 2.0 * (c.railTravelMm + 12.0);
    add("rail_x", "mechanical", "ballast_linear_guide_x", "al_6061_t6", 0.025,
        centeredBox(railLength, 4, 4, 0, 0, 4));
    add("rail_y", "mechanical", "ballast_linear_guide_y", "al_6061_t6", 0.025,
        centeredBox(4, railLength, 4, 0, 0, 4));
    const double strutX[] = {-46.0, 46.0, -46.0, 46.0};
    const double strutY[] = {-28.0, -28.0, 28.0, 28.0};
    for (int i = 0; i < 4; ++i) {
        add("frame_strut_" + std::to_string(i + 1), "mechanical", "internal_frame_strut",
            "al_6061_t6", 0.010,
            cylinderBetween(gp_Pnt(strutX[i], strutY[i], -42),
                            gp_Pnt(strutX[i], strutY[i], 42), 1.8));
    }
    const double motorEnd = -c.railTravelMm - 15.0;
    add("motor_x", "electromechanical", "x_axis_gearmotor_encoder", "generic_component", 0.080,
        cylinderBetween(gp_Pnt(motorEnd - 18.0, -8, 5), gp_Pnt(motorEnd, -8, 5), 7.5));
    add("motor_y", "electromechanical", "y_axis_gearmotor_encoder", "generic_component", 0.080,
        cylinderBetween(gp_Pnt(8, motorEnd - 18.0, 5), gp_Pnt(8, motorEnd, 5), 7.5));
    add("ballast_carriage", "mechanical", "xy_carriage", "al_6061_t6", 0.040,
        centeredBox(24, 24, 4, command.targetXmm, command.targetYmm, 8));
    add("ballast", "mechanical", "movable_mass", "tungsten_ballast", 0.0,
        BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(command.targetXmm, command.targetYmm, 10),
                                        gp_Dir(0, 0, 1)), 9.5, 16).Shape());

    add("controller_pcb", "electronic", "four_layer_control_pcb", "fr4", 0.055,
        centeredBox(54, 40, 1.6, 0, 0, -29));
    add("mcu", "electronic", "real_time_controller", "generic_component", 0.004,
        centeredBox(12, 12, 2, -12, 5, -27.2));
    add("imu", "electronic", "six_axis_inertial_sensor", "generic_component", 0.002,
        centeredBox(5, 5, 1.2, 0, 0, -27.6));
    add("motor_driver_x", "electronic", "x_axis_h_bridge", "generic_component", 0.006,
        centeredBox(10, 10, 2, 13, 8, -27.2));
    add("motor_driver_y", "electronic", "y_axis_h_bridge", "generic_component", 0.006,
        centeredBox(10, 10, 2, 13, -8, -27.2));
    add("power_regulator", "electronic", "buck_5v_3v3", "generic_component", 0.004,
        centeredBox(12, 8, 3, -12, -9, -26.7));
    add("battery", "electrical", "three_series_li_ion_pack", "generic_component", 0.220,
        centeredBox(48, 28, 12, 0, 0, -48));
    add("fuse", "electrical", "battery_fuse_5a", "generic_component", 0.004,
        centeredBox(12, 5, 5, 30, -8, -38));
    add("power_switch", "electrical", "service_disconnect", "generic_component", 0.006,
        centeredBox(10, 8, 8, 31, 8, -38));
    add("harness_batt_pos", "electrical", "fused_battery_positive", "generic_component", 0.005,
        cylinderBetween(gp_Pnt(20, -7, -43), gp_Pnt(20, -7, -30), 0.8));
    add("harness_ground", "electrical", "battery_ground", "generic_component", 0.005,
        cylinderBetween(gp_Pnt(-20, -7, -43), gp_Pnt(-20, -7, -30), 0.8));

    double baseFixedMass = 0.0;
    for (const Part& part : parts)
        if (part.id != "ballast") baseFixedMass += part.baseMassKg;
    const double scale = c.fixedMassKg / baseFixedMass;
    for (Part& part : parts)
        part.massKg = part.id == "ballast" ? c.ballastMassKg : part.baseMassKg * scale;
    return parts;
}

} // namespace

bool balancingSphereCadAvailable() noexcept { return true; }

bool generateBalancingSpherePackage(const BalancingSphereConfig& config,
                                    const std::string& outputDirectory,
                                    BalancingSpherePackageResult& result,
                                    std::string& error)
{
    result = {};
    if (!validateBalancingSphereConfig(config, error)) return false;
    if (outputDirectory.empty()) { error = "output directory is empty"; return false; }
    try {
        const fs::path output = fs::absolute(outputDirectory);
        const fs::path partDirectory = output / "parts";
        std::error_code ec;
        fs::create_directories(partDirectory, ec);
        if (ec) { error = "cannot create package directory: " + ec.message(); return false; }

        const BalanceCommand command = computeBalanceCommand(
            config, config.surfaceRollDeg, config.surfacePitchDeg,
            config.rollRateDegS, config.pitchRateDegS);
        std::vector<Part> parts = buildParts(config, command);
        int validated = 0;
        for (const Part& part : parts) {
            if (!validSolidShape(part.shape)) {
                error = "generated part is not a valid B-Rep solid: " + part.id;
                return false;
            }
            ++validated;
            if (!writeAndRoundTripStep({part.shape}, partDirectory / (part.id + ".step"), error))
                return false;
        }

        const TopoDS_Compound assembly = compoundOf(parts, false);
        const TopoDS_Compound cutaway = compoundOf(parts, true);
        const fs::path assemblyPath = output / "self_balancing_sphere_assembly.step";
        const fs::path cutawayPath = output / "self_balancing_sphere_cutaway.step";
        if (!writeAndRoundTripStep({assembly}, assemblyPath, error)
            || !writeAndRoundTripStep({cutaway}, cutawayPath, error)) return false;

        Bnd_Box bounds;
        BRepBndLib::AddOptimal(assembly, bounds);
        if (bounds.IsVoid()) { error = "assembly has no finite bounding box"; return false; }
        double xmin = 0, ymin = 0, zmin = 0, xmax = 0, ymax = 0, zmax = 0;
        bounds.Get(xmin, ymin, zmin, xmax, ymax, zmax);
        const double tolerance = 0.05;
        if (std::abs(xmin + config.outerRadiusMm) > tolerance
            || std::abs(ymin + config.outerRadiusMm) > tolerance
            || std::abs(zmin + config.outerRadiusMm) > tolerance
            || std::abs(xmax - config.outerRadiusMm) > tolerance
            || std::abs(ymax - config.outerRadiusMm) > tolerance
            || std::abs(zmax - config.outerRadiusMm) > tolerance) {
            error = "assembly bounding box does not match the configured sphere diameter";
            return false;
        }

        const fs::path projectPath = output / "self_balancing_sphere.dsproj";
        const fs::path csvPath = output / "controller_response.csv";
        const std::vector<BalanceSample> response = simulateBallastResponse(config, 2.0, 0.01);
        if (response.empty() || !writeProject(projectPath, config, command, parts, error)
            || !writeControllerCsv(csvPath, response, error)) return false;

        result.assemblyStepPath = assemblyPath.string();
        result.cutawayStepPath = cutawayPath.string();
        result.projectPath = projectPath.string();
        result.controllerCsvPath = csvPath.string();
        result.partCount = static_cast<int>(parts.size());
        result.validatedSolidCount = validated;
        result.totalMassKg = config.fixedMassKg + config.ballastMassKg;
        result.maximumStaticAngleDeg = command.maximumStaticAngleDeg;
        result.initialCommand = command;
        error.clear();
        return true;
    } catch (const Standard_Failure& failure) {
        error = std::string("Open CASCADE failure: ")
            + (failure.GetMessageString() ? failure.GetMessageString() : "unknown");
        return false;
    } catch (const std::exception& exception) {
        error = exception.what();
        return false;
    } catch (...) {
        error = "unknown balancing-sphere generation failure";
        return false;
    }
}

} // namespace dc::mechatronics

#endif // HAS_OCCT
