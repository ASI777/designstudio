#include "KicadSymbolLibrary.h"

#include <QCoreApplication>
#include <QFile>
#include <QTemporaryDir>

#include <iostream>

namespace {

bool check(bool condition, const char* message)
{
    if (condition) return true;
    std::cerr << "FAIL: " << message << '\n';
    return false;
}

bool writeFile(const QString& path, const QByteArray& contents)
{
    QFile file(path);
    return file.open(QIODevice::WriteOnly | QIODevice::Truncate)
        && file.write(contents) == contents.size();
}

} // namespace

int main(int argc, char** argv)
{
    QCoreApplication application(argc, argv);
    QTemporaryDir directory;
    if (!directory.isValid()) return 2;

    const QByteArray library = R"KICAD((kicad_symbol_lib
  (version 20231120)
  (generator test_fixture)
  (symbol "Device:R"
    (symbol "R_0_1"
      (rectangle (start -1.0 2.0) (end 1.0 -2.0)
        (stroke (width 0.25) (type default)) (fill (type none)))
      (polyline (pts (xy -1 0) (xy 0 1) (xy 1 0))
        (stroke (width 0.2) (type default)) (fill (type none))))
    (symbol "R_1_1"
      (pin passive line (at 0 3.81 270) (length 1.27)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))))))
)KICAD";
    const QString libraryPath = directory.filePath(QStringLiteral("Device.kicad_sym"));
    if (!writeFile(libraryPath, library)) return 2;

    designstudio::KicadSymbolLibrary symbols;
    QString error;
    bool ok = check(symbols.loadFile(libraryPath, &error), "valid .kicad_sym rejected")
        && check(symbols.definition(QStringLiteral("Device:R")) != nullptr,
                 "full KiCad library identifier not indexed")
        && check(symbols.definition(QStringLiteral("R")) != nullptr,
                 "leaf KiCad library identifier not indexed")
        && check(symbols.definition(QStringLiteral("R"))->graphics.size() == 2,
                 "drawable primitives not extracted")
        && check(symbols.definition(QStringLiteral("R"))->boundsMm.width() > 1.9,
                 "symbol bounds not computed");

    const QByteArray schematic = R"KICAD((kicad_sch
  (version 20231120)
  (generator test_fixture)
  (lib_symbols
    (symbol "Switch:SW_Push"
      (symbol "SW_Push_0_1"
        (circle (center 0 0) (radius 1.5)
          (stroke (width 0.2) (type default)) (fill (type background)))))))
)KICAD";
    const QString schematicPath = directory.filePath(QStringLiteral("fixture.kicad_sch"));
    if (!writeFile(schematicPath, schematic)) return 2;
    ok = check(symbols.loadFile(schematicPath, &error),
               "embedded kicad_sch lib_symbols rejected") && ok;
    ok = check(symbols.definition(QStringLiteral("SW_Push")) != nullptr,
               "embedded schematic symbol not indexed") && ok;

    const QString malformedPath = directory.filePath(QStringLiteral("bad.kicad_sym"));
    if (!writeFile(malformedPath, QByteArray("(kicad_symbol_lib (symbol \"x\""))) return 2;
    ok = check(!symbols.loadFile(malformedPath, &error)
                   && error.contains(QStringLiteral("Unterminated")),
               "malformed KiCad S-expression accepted") && ok;
    return ok ? 0 : 1;
}
