#!/usr/bin/env python3
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from swarm.release.release_gate import validate_release
from swarm.release.signing import key_id_from_public, public_key_base64, sign_manifest
from swarm.memory.incremental_design import bind_analysis_to_project
from swarm.memory.component_binding import bind_component

binary, fixture = sys.argv[1:3]
now = datetime.now(timezone.utc)


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


with tempfile.TemporaryDirectory() as raw:
    directory = Path(raw)
    project = json.loads(Path(fixture).read_text())
    pdf = directory / "ABC-123.pdf"; pdf.write_bytes(b"%PDF-1.7\nABC-123 verified datasheet\n%%EOF")
    evidence = {"schema": "design-studio.datasheet-evidence/1", "source_kind": "local",
                "source": str(pdf), "retrieved_utc": now.isoformat(), "bytes": pdf.stat().st_size,
                "sha256": sha(pdf), "expected_mpn": "ABC-123", "mpn_match": "exact"}
    for fp in project["footprints"]:
        fp.update({"mpn": "ABC-123", "manufacturer": "Acme",
                   "datasheet_evidence": evidence})
    project["pcb_rules"] = {
        "schema_version": 1, "id": "fab-class2-b", "name": "Fixture fab profile",
        "ipc_performance_class": 2, "producibility_level": "B",
        "source": "fabricator:fixture", "source_revision": "2026-07",
        "fabricator": "Fixture PCB", "assembler": "Fixture Assembly",
        "limits_mm": {"default_clearance":0.2,"min_trace_width":0.1,
            "min_mechanical_drill":0.15,"min_annular_ring":0.1,"min_drill_to_drill":0.25,
            "min_microvia_drill":0.075,"min_microvia_wall":0.1,"min_copper_to_edge":0.25,
            "min_copper_to_hole":0.25,"min_courtyard_clearance":0.25,
            "min_mask_sliver":0.1,"min_silk_width":0.1},
        "checks":{"connectivity":True,"skew":True,"release_requires_native_drc":True},
        "severity":{}
    }
    project["unresolved_components"] = []
    component_step = directory / "ABC-123.step"
    component_step.write_text("ISO-10303-21;\nHEADER;ENDSEC;DATA;ENDSEC;END-ISO-10303-21;\n")
    component_definition = {
        "schema": "design-studio.component/2",
        "component": {"mpn": "ABC-123", "manufacturer": "Acme"},
        "symbol": {"pins": [{"number": "1", "name": "IO", "electrical_type": "passive"}]},
        "footprint": {"pads": [{"number": "1", "x_mm": 0.0, "y_mm": 0.0,
                                "width_mm": 1.0, "height_mm": 1.0, "shape": "rect"}],
                      "body": {"width_mm": 1.0, "length_mm": 1.0}},
        "electrical": {"kind": "passive-terminal",
                       "pin_functions": [{"pin": "1", "function": "IO"}]},
        "evidence": {"package_pin_count": 1, "package_variant": "TEST-1"},
    }
    component_binding = bind_component(
        component_definition, component_step, alignment_status="verified",
        model_mpn="ABC-123", source_uri="fixture:ABC-123")
    component_record = directory / "ABC-123.bound.json"
    component_record.write_text(json.dumps(component_binding, indent=2, sort_keys=True) + "\n")
    for fp in project["footprints"]:
        fp["bound_component"] = {
            "schema": "design-studio.bound-component-ref/1",
            "binding_id": component_binding["binding_id"],
            "binding_digest": component_binding["binding_digest"],
            "record_uri": component_record.name,
            "model_3d": component_binding["model_3d"]}
    project_path = directory / "release.dsproj"
    project_path.write_text(json.dumps(project, separators=(",", ":")))
    verification_path = directory / "verification.json"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", DS_AI_MOCK="1")
    result = subprocess.run([binary, "--smoke-test", "--verification-out",
                             str(verification_path), str(project_path)], env=env,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    assert result.returncode == 0, result.stdout
    verification = json.loads(verification_path.read_text())
    assert verification["overall_status"] == "pass", verification
    analysis_path = directory / "electrical-analysis.json"
    analysis = {"schema":"design-studio.incremental-analysis/1", "revision":1,
        "status":"pass", "changed_refs":[],
        "recomputed_nets":[], "categories":{
            "erc":{"status":"pass","findings":[]},
            "dc_operating_point":{"status":"pass","findings":[]},
            "power_tree":{"status":"pass","findings":[]},
            "stability":{"status":"not_applicable","findings":[]},
            "spice":{"status":"not_applicable","findings":[]}},
        "schematic":{"symbols":[],"wires":[]}}
    bind_analysis_to_project(analysis, project_path, analysis_path)

    quote_path = directory / "quote.json"
    quote_path.write_text(json.dumps({"schema":"design-studio.procurement-quote/1",
        "mpn":"ABC-123","quantity":2,"retrieved_utc":now.isoformat(),"complete":True,
        "vendor_errors":{},"offers":[{"vendor":"digikey","mpn":"ABC-123","sku":"DK-1",
        "stock":100,"price":{"quantity":2,"unit_price":1.0,"currency":"USD",
        "price_inr_estimate":83.0,"fx_profile":"test"},"retrieved_utc":now.isoformat(),
        "exact_mpn_match":True}]}))

    gerber = directory / "gerbers.zip"
    with zipfile.ZipFile(gerber, "w") as archive: archive.writestr("board-F_Cu.gbr", "%FSLAX46Y46*%\nM02*")
    files = {"gerber_archive": gerber, "drill": directory/"board.drl",
             "bom": directory/"BOM.csv", "pick_place": directory/"PnP.csv",
             "board_step": directory/"board.step"}
    files["drill"].write_text("M48\nMETRIC,TZ\nM30\n")
    files["bom"].write_text("MPN,Qty,Refs\nABC-123,2,J1 J2\n")
    files["pick_place"].write_text("Ref,MPN,X_mm,Y_mm\nJ1,ABC-123,5,10\n")
    files["board_step"].write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n")
    artifacts = [{"role":role,"path":path.name,"sha256":sha(path),
                  "generator":"kicad","generator_version":"8.0"}
                 for role,path in files.items()]
    freecad_review = directory / "freecad-license-review.json"
    freecad_review.write_text(json.dumps({"review": "LGPL distribution obligations",
                                          "decision": "approved for fixture"}))
    hunyuan_review = directory / "hunyuan-model-review.json"
    hunyuan_review.write_text(json.dumps({"review": "model-use and distribution terms",
                                          "decision": "approved for fixture"}))
    signing_key = Ed25519PrivateKey.generate()
    public_key = public_key_base64(signing_key)
    signing_key_id = key_id_from_public(public_key)
    trust_store = directory / "release-trust.json"
    trust_store.write_text(json.dumps({"schema": "design-studio.release-trust/1",
                                       "keys": {signing_key_id: public_key}}))
    os.environ["DESIGNSTUDIO_RELEASE_TRUST_STORE"] = str(trust_store)
    manifest = {"schema":"design-studio.release-manifest/1",
        "project":{"document_id":project["document_id"],"revision":project["revision"],
                   "sha256":sha(project_path)},
        "verification":{"path":verification_path.name,"sha256":sha(verification_path)},
        "electrical_analysis":{"path":analysis_path.name,"sha256":sha(analysis_path)},
        "components":[{"ref":fp["ref"],"mpn":"ABC-123","datasheet_path":pdf.name,
                       "quote_path":quote_path.name,"quote_sha256":sha(quote_path)}
                      for fp in project["footprints"]],
        "artifacts":artifacts,
        "approvals":[{"role":role,"approved":True,"reviewer":"Fixture reviewer","utc":now.isoformat(),
                      "project_sha256":sha(project_path),"verification_sha256":sha(verification_path)}
                     for role in ("manufacturing_review","mechanical_review",
                                  "engineering_review","electrical_review")],
        "compliance_reviews":[
            {"subject":"freecad-distribution","status":"approved","reviewer":"Fixture Counsel",
             "distribution_authorized":True,"evidence_path":freecad_review.name,
             "evidence_sha256":sha(freecad_review),"utc":now.isoformat()},
            {"subject":"hunyuan3d-omni-model","status":"approved","reviewer":"Fixture Counsel",
             "distribution_authorized":True,"evidence_path":hunyuan_review.name,
             "evidence_sha256":sha(hunyuan_review),"utc":now.isoformat()}]}
    manifest_path = directory / "manifest.json"

    def write_manifest():
        signed = sign_manifest(manifest, signing_key)
        manifest.clear()
        manifest.update(signed)
        manifest_path.write_text(json.dumps(manifest))

    write_manifest()

    ready = validate_release(str(project_path), str(verification_path), str(manifest_path), now=now)
    assert ready["status"] == "ready", ready

    axis_reports = [{"axis_id":f"axis-{index}", "input_digest":f"{index}" * 64,
                     "output_digest":f"{index + 1}" * 64, "status":"pass", "checks":[]}
                    for index in range(1, 7)]
    coordinator_report = {"board_id":"coordinator", "input_digest":"a" * 64,
                          "output_digest":"b" * 64, "status":"pass", "checks":[]}
    system_report = {"input_digest":"c" * 64, "output_digest":"d" * 64,
                     "coordinator_output_digest":"b" * 64,
                     "axis_output_digests":[item["output_digest"] for item in axis_reports],
                     "status":"pass"}
    distributed = {"schema":"design-studio.distributed-robot-analysis/1",
                   "status":"pass", "evidence_language":{"physical_measurement":False},
                   "axis_reports":axis_reports, "coordinator_report":coordinator_report,
                   "categories":{}, "system_report":system_report, "release_blockers":[]}
    distributed["report_digest"] = hashlib.sha256(json.dumps(
        distributed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    distributed_path = directory / "distributed-analysis.json"
    distributed_path.write_text(json.dumps(distributed))
    board_projects = []
    board_hashes = {}
    for board_id in ["coordinator"] + [f"joint-{index}" for index in range(1, 7)]:
        board_path = directory / f"{board_id}.dsproj"
        board_path.write_text(json.dumps({"board_id":board_id,"revision":1}))
        board_hashes[board_id] = sha(board_path)
        board_projects.append({"board_id":board_id,"path":board_path.name,
                               "sha256":board_hashes[board_id]})
    manifest["distributed_analysis"] = {
        "path":distributed_path.name,"sha256":sha(distributed_path)}
    manifest["board_projects"] = board_projects
    manifest["distributed_approvals"] = [
        {"scope":scope,"role":role,"approved":True,"reviewer":"Fixture reviewer",
         "utc":now.isoformat(),
         "project_sha256":sha(project_path) if scope == "system" else board_hashes[scope],
         "analysis_sha256":sha(distributed_path)}
        for scope in ["system", "coordinator"] + [f"joint-{index}" for index in range(1, 7)]
        for role in ("engineering_review","electrical_review",
                     "mechanical_review","manufacturing_review")]
    write_manifest()
    distributed_ready = validate_release(
        str(project_path), str(verification_path), str(manifest_path), now=now)
    assert distributed_ready["status"] == "ready", distributed_ready
    manifest.pop("distributed_analysis")
    manifest.pop("board_projects")
    manifest.pop("distributed_approvals")

    original_analysis = analysis_path.read_bytes()
    incomplete_analysis = json.loads(original_analysis)
    incomplete_analysis["status"] = "incomplete"
    incomplete_analysis["categories"]["spice"] = {
        "status": "incomplete", "findings": [{"code": "SOLVER_MISSING"}]}
    bind_analysis_to_project(incomplete_analysis, project_path, analysis_path)
    manifest["electrical_analysis"]["sha256"] = sha(analysis_path)
    write_manifest()
    incomplete = validate_release(str(project_path), str(verification_path), str(manifest_path), now=now)
    assert incomplete["status"] == "blocked"
    assert any(e["code"] == "ELECTRICAL_CATEGORY_INCOMPLETE" for e in incomplete["errors"])

    manifest["incomplete_acknowledgements"] = [{
        "domain": "electrical", "category": "spice", "acknowledged": True,
        "reviewer": "Fixture EE", "rationale": "Bench correlation is pending",
        "utc": now.isoformat()}]
    write_manifest()
    acknowledged = validate_release(str(project_path), str(verification_path),
                                    str(manifest_path), now=now)
    assert acknowledged["status"] == "blocked", acknowledged
    assert any(w["code"] == "INCOMPLETE_ACK_NO_RELEASE_AUTHORITY"
               for w in acknowledged["warnings"])
    analysis_path.write_bytes(original_analysis)
    manifest["electrical_analysis"]["sha256"] = sha(analysis_path)
    manifest.pop("incomplete_acknowledgements")
    write_manifest()

    changed_analysis = json.loads(original_analysis)
    changed_analysis["categories"]["erc"]["findings"] = [{"code": "TAMPER"}]
    analysis_path.write_text(json.dumps(changed_analysis))
    manifest["electrical_analysis"]["sha256"] = sha(analysis_path)
    write_manifest()
    analysis_tamper = validate_release(str(project_path), str(verification_path), str(manifest_path), now=now)
    assert analysis_tamper["status"] == "blocked"
    assert any(e["code"] == "ELECTRICAL_REPORT_DIGEST" for e in analysis_tamper["errors"])
    analysis_path.write_bytes(original_analysis)
    manifest["electrical_analysis"]["sha256"] = sha(analysis_path)
    write_manifest()

    original_component_step = component_step.read_bytes()
    component_step.write_bytes(original_component_step + b"tamper")
    changed_step = validate_release(str(project_path), str(verification_path), str(manifest_path), now=now)
    assert changed_step["status"] == "blocked"
    assert any(e["code"] == "COMPONENT_STEP" for e in changed_step["errors"])
    component_step.write_bytes(original_component_step)

    original_component_record = component_record.read_bytes()
    changed_record = json.loads(original_component_record)
    changed_record["electrical"] = {}
    component_record.write_text(json.dumps(changed_record))
    changed_semantics = validate_release(
        str(project_path), str(verification_path), str(manifest_path), now=now)
    assert changed_semantics["status"] == "blocked"
    assert any(e["code"] == "COMPONENT_SEMANTICS" for e in changed_semantics["errors"])
    component_record.write_bytes(original_component_record)

    original_drill = files["drill"].read_bytes(); files["drill"].write_bytes(original_drill + b"tamper")
    tampered_artifact = validate_release(str(project_path), str(verification_path), str(manifest_path), now=now)
    assert tampered_artifact["status"] == "blocked"
    assert any(e["code"] == "ARTIFACT_DIGEST" for e in tampered_artifact["errors"])
    files["drill"].write_bytes(original_drill)

    original_project = project_path.read_bytes(); project_path.write_bytes(original_project + b" ")
    stale = validate_release(str(project_path), str(verification_path), str(manifest_path), now=now)
    assert stale["status"] == "blocked"
    assert any(e["code"] == "STALE_VERIFICATION" for e in stale["errors"])
    project_path.write_bytes(original_project)

    manifest["project"]["revision"] += 1
    manifest_path.write_text(json.dumps(manifest))
    unsigned_tamper = validate_release(str(project_path), str(verification_path),
                                       str(manifest_path), now=now)
    assert unsigned_tamper["status"] == "blocked"
    assert any(e["code"] == "MANIFEST_SIGNATURE" for e in unsigned_tamper["errors"])
    manifest["project"]["revision"] -= 1

    manifest["distributed_analysis"] = {
        "path": "missing-distributed-analysis.json", "sha256": "0" * 64}
    write_manifest()
    missing_distributed = validate_release(
        str(project_path), str(verification_path), str(manifest_path), now=now)
    assert missing_distributed["status"] == "blocked"
    assert any(e["code"] == "DISTRIBUTED_ANALYSIS"
               for e in missing_distributed["errors"])
    assert any(e["code"] == "DISTRIBUTED_BOARD_SET"
               for e in missing_distributed["errors"])
    manifest.pop("distributed_analysis")

    manifest["artifacts"][0]["generator"] = "designstudio-preview-fab-export"
    write_manifest()
    preview = validate_release(str(project_path), str(verification_path), str(manifest_path), now=now)
    assert preview["status"] == "blocked"
    assert any(e["code"] == "ARTIFACT_GENERATOR" for e in preview["errors"])
    print("Release gate signed/compliance/electrical/component/stale/artifact/preview tests passed")
