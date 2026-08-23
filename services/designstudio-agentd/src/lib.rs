//! DesignStudio's local, fail-closed product control plane.
//!
//! Geometry and electronics remain in their native documents. This service owns
//! semantic identity, immutable configuration snapshots, jobs, evidence state,
//! selection, and release evaluation. It intentionally exposes typed operations
//! instead of an arbitrary Python or shell execution surface.

use chrono::{SecondsFormat, Utc};
use jsonschema::{Draft, JSONSchema};
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Component, Path, PathBuf};
use uuid::Uuid;

pub const API_VERSION: &str = "design-studio.agentd/1";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceManifest {
    pub schema: String,
    pub workspace_id: Uuid,
    pub revision: u64,
    pub product: ProductIdentity,
    pub documents: Documents,
    pub product_graph: String,
    pub baseline_configuration: String,
    #[serde(default)]
    pub contracts: Vec<String>,
    #[serde(default)]
    pub evidence_directories: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProductIdentity {
    pub name: String,
    #[serde(default)]
    pub description: String,
}

/// Plain-language product discovery contract.  This is deliberately separate
/// from the product graph: it describes a proposed product before any CAD,
/// schematic, or PCB document is touched.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplicationCatalogEntry {
    pub family: String,
    pub name: String,
    pub starter_label: String,
    pub summary: String,
    pub ranked_decisions: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplicationQuestion {
    pub id: String,
    pub prompt: String,
    pub why_it_matters: String,
    pub choices: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplicationCheck {
    pub name: String,
    pub status: String,
    pub message: String,
    pub required_for_release: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplicationOption {
    pub id: String,
    pub name: String,
    pub best_for: String,
    pub benefits: Vec<String>,
    pub drawbacks: Vec<String>,
    pub estimates: Map<String, Value>,
    pub assumptions: Vec<String>,
    pub what_remains_to_be_proven: Vec<String>,
    pub applicable_checks: Vec<ApplicationCheck>,
    pub evidence_state: String,
    pub unresolved_gates: Vec<String>,
    #[serde(default)]
    pub failed_gates: Vec<String>,
    pub technical_details: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplicationConfiguration {
    pub schema: String,
    pub family: String,
    pub family_name: String,
    pub confidence: f64,
    pub custom_concept: bool,
    pub prompt: String,
    pub captured_requirements: Map<String, Value>,
    pub assumptions: Vec<String>,
    pub next_questions: Vec<ApplicationQuestion>,
    pub options: Vec<ApplicationOption>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub axis_requirements: Vec<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub system_architecture: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Documents {
    pub mechanical: String,
    pub electronics: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProductGraph {
    pub schema: String,
    pub product_id: Uuid,
    pub revision: u64,
    pub nodes: Vec<ProductNode>,
    pub edges: Vec<ProductEdge>,
    pub slots: Vec<Slot>,
    pub variants: Vec<Variant>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProductNode {
    pub id: Uuid,
    pub name: String,
    pub assembly_path: String,
    pub domain: String,
    pub authority: String,
    pub source_ref: String,
    #[serde(default)]
    pub requirements: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProductEdge {
    pub from: Uuid,
    pub to: Uuid,
    pub kind: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Slot {
    pub id: String,
    pub name: String,
    pub node_id: Uuid,
    pub assembly_path: String,
    pub allowed_change_class: String,
    pub protected_properties: Vec<String>,
    pub customizable_properties: Vec<String>,
    pub required_evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Variant {
    pub id: String,
    pub slot_id: String,
    pub name: String,
    pub change_class: String,
    #[serde(default)]
    pub parameters: Map<String, Value>,
    #[serde(default)]
    pub failed_gates: Vec<String>,
    #[serde(default)]
    pub incomplete_gates: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ConfigurationState {
    Sandbox,
    Committed,
    Released,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Configuration {
    pub schema: String,
    pub configuration_id: Uuid,
    pub revision: u64,
    pub parent_configuration_id: Option<Uuid>,
    pub baseline_configuration_id: Uuid,
    pub workspace_revision: u64,
    pub graph_revision: u64,
    pub state: ConfigurationState,
    pub equipped: BTreeMap<String, String>,
    #[serde(default)]
    pub parameter_overrides: Map<String, Value>,
    pub requirement_profile: String,
    pub created_utc: String,
    pub digest: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EvidenceRecord {
    pub schema: String,
    pub evidence_id: Uuid,
    pub configuration_id: Uuid,
    pub gate: String,
    pub status: EvidenceStatus,
    pub method: String,
    pub source_run: String,
    pub input_digest: String,
    #[serde(default)]
    pub value: Option<Value>,
    #[serde(default)]
    pub unit: Option<String>,
    #[serde(default)]
    pub requirement: Option<String>,
    #[serde(default)]
    pub margin: Option<f64>,
    #[serde(default)]
    pub uncertainty: Option<Value>,
    #[serde(default)]
    pub assumptions: Vec<String>,
    #[serde(default)]
    pub dependencies: Vec<String>,
    pub created_utc: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceStatus {
    Pass,
    Fail,
    Incomplete,
    NotApplicable,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Job {
    pub job_id: Uuid,
    pub kind: String,
    pub configuration_id: Uuid,
    #[serde(default)]
    pub slot_id: Option<String>,
    pub status: JobStatus,
    pub input_digest: String,
    pub created_utc: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum JobStatus {
    Queued,
    Running,
    Succeeded,
    Failed,
    Cancelled,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContractRevision {
    pub contract_id: String,
    pub version: u64,
    pub digest: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContractChange {
    pub contract_id: String,
    pub base_version: u64,
    pub proposed_version: u64,
    pub digest: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProposalBranch {
    pub schema: String,
    pub branch_id: Uuid,
    pub name: String,
    pub domain: String,
    pub baseline_configuration_id: Uuid,
    pub head_configuration_id: Uuid,
    pub source_workspace_revision: u64,
    pub source_graph_revision: u64,
    pub consumes_contracts: Vec<ContractRevision>,
    pub proposes_contract_changes: Vec<ContractChange>,
    pub artifacts: Vec<String>,
    pub evidence_ids: Vec<Uuid>,
    pub assumptions: Vec<String>,
    pub requested_approvals: Vec<String>,
    pub created_by: String,
    pub created_utc: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum IntegrationStatus {
    Draft,
    Blocked,
    ReadyForApproval,
    Approved,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IntegrationApproval {
    pub role: String,
    pub approver: String,
    pub created_utc: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IntegrationSandbox {
    pub schema: String,
    pub integration_id: Uuid,
    pub baseline_configuration_id: Uuid,
    pub branch_heads: BTreeMap<Uuid, Uuid>,
    pub status: IntegrationStatus,
    pub checks: Vec<Value>,
    pub required_approvals: BTreeSet<String>,
    pub approvals: BTreeMap<String, IntegrationApproval>,
    pub created_by: String,
    pub created_utc: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskBudget {
    pub max_steps: u64,
    pub max_seconds: u64,
    pub max_cost_microunits: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AgentTaskStatus {
    Queued,
    Running,
    Cancelled,
    Succeeded,
    Failed,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AgentTask {
    pub schema: String,
    pub task_id: Uuid,
    pub branch_id: Uuid,
    pub agent_id: String,
    pub kind: String,
    pub status: AgentTaskStatus,
    pub budget: TaskBudget,
    pub consumed_steps: u64,
    pub consumed_seconds: u64,
    pub consumed_cost_microunits: u64,
    pub recovery_count: u8,
    pub created_utc: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProductIntent {
    pub schema: String,
    pub product_id: String,
    pub application_family: String,
    pub name: String,
    pub source_product: SourceProductBinding,
    pub requirements: ConsoleRequirements,
    pub budget_units: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceProductBinding {
    pub product: String,
    pub source_root: String,
    pub project_path: String,
    pub project_digest: String,
    pub acceptance_report_path: String,
    pub acceptance_report_digest: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConsoleRequirements {
    pub usb_c_connector_mpn: String,
    pub ble_mcu_module_mpn: String,
    pub host_transport: String,
    pub wireless_transport: String,
    pub local_backend: bool,
    pub gpu_allowed: bool,
    pub architecture_approval_required: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkPackage {
    pub id: String,
    pub name: String,
    pub engine_id: String,
    pub depends_on: Vec<String>,
    pub input_refs: Vec<String>,
    pub budget_units: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkPackageGraph {
    pub schema: String,
    pub graph_id: String,
    pub product_id: String,
    pub product_digest: String,
    pub runnable: bool,
    pub approval_gate: String,
    pub packages: Vec<WorkPackage>,
    pub graph_digest: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkflowApproval {
    pub kind: String,
    pub approver: String,
    pub product_digest: String,
    pub graph_digest: String,
    pub approval_digest: String,
    pub approved_utc: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkflowCheckpoint {
    pub package_id: String,
    pub status: String,
    pub input_digest: Option<String>,
    pub output_digest: Option<String>,
    pub result_ref: Option<String>,
    pub consumed_units: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkflowBudget {
    pub allocated_units: u64,
    pub consumed_units: u64,
    pub remaining_units: u64,
    pub rejected_packages: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkflowRun {
    pub schema: String,
    pub workflow_id: String,
    pub product_digest: String,
    pub graph_digest: String,
    pub status: String,
    pub approval: Option<WorkflowApproval>,
    pub checkpoints: Vec<WorkflowCheckpoint>,
    pub budget: WorkflowBudget,
    pub created_utc: String,
    pub updated_utc: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct PersistedState {
    schema: String,
    workspace_id: Option<Uuid>,
    workspace_revision: u64,
    daemon_revision: u64,
    configurations: BTreeMap<Uuid, Configuration>,
    evidence: Vec<EvidenceRecord>,
    jobs: BTreeMap<Uuid, Job>,
    #[serde(default)]
    branches: BTreeMap<Uuid, ProposalBranch>,
    #[serde(default)]
    integrations: BTreeMap<Uuid, IntegrationSandbox>,
    #[serde(default)]
    agent_tasks: BTreeMap<Uuid, AgentTask>,
    #[serde(default)]
    product_intents: BTreeMap<String, ProductIntent>,
    #[serde(default)]
    workflow_graphs: BTreeMap<String, WorkPackageGraph>,
    #[serde(default)]
    interaction_maps: BTreeMap<String, Value>,
    #[serde(default)]
    workflows: BTreeMap<String, WorkflowRun>,
    selection: Option<Uuid>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RpcRequest {
    pub jsonrpc: String,
    pub id: Value,
    pub method: String,
    #[serde(default)]
    pub params: Value,
    pub auth: RpcAuth,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RpcAuth {
    pub permissions: BTreeSet<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct RpcResponse {
    pub jsonrpc: &'static str,
    pub id: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<RpcFault>,
}

#[derive(Debug, Clone, Serialize)]
pub struct RpcFault {
    pub code: i64,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
}

impl RpcFault {
    fn invalid(message: impl Into<String>) -> Self {
        Self {
            code: -32602,
            message: message.into(),
            data: None,
        }
    }

    fn state(message: impl Into<String>) -> Self {
        Self {
            code: -32010,
            message: message.into(),
            data: None,
        }
    }

    fn forbidden(permission: &str) -> Self {
        Self {
            code: -32003,
            message: format!("permission '{permission}' is required"),
            data: Some(json!({"required_permission": permission})),
        }
    }

    fn io(context: &str, error: impl std::fmt::Display) -> Self {
        Self {
            code: -32020,
            message: format!("{context}: {error}"),
            data: None,
        }
    }
}

pub struct ControlPlane {
    manifest_path: Option<PathBuf>,
    workspace_root: Option<PathBuf>,
    manifest: Option<WorkspaceManifest>,
    graph: Option<ProductGraph>,
    state: PersistedState,
}

impl Default for ControlPlane {
    fn default() -> Self {
        Self {
            manifest_path: None,
            workspace_root: None,
            manifest: None,
            graph: None,
            state: PersistedState {
                schema: "design-studio.agent-state/1".into(),
                ..Default::default()
            },
        }
    }
}

impl ControlPlane {
    pub fn handle_json(&mut self, line: &str) -> String {
        let parsed = serde_json::from_str::<RpcRequest>(line);
        let (id, outcome) = match parsed {
            Ok(request) => {
                let id = request.id.clone();
                (id, self.handle(request))
            }
            Err(error) => (
                Value::Null,
                Err(RpcFault {
                    code: -32700,
                    message: format!("invalid JSON-RPC request: {error}"),
                    data: None,
                }),
            ),
        };
        let response = match outcome {
            Ok(result) => RpcResponse {
                jsonrpc: "2.0",
                id,
                result: Some(result),
                error: None,
            },
            Err(error) => RpcResponse {
                jsonrpc: "2.0",
                id,
                result: None,
                error: Some(error),
            },
        };
        serde_json::to_string(&response).expect("response serialization cannot fail")
    }

    pub fn handle(&mut self, request: RpcRequest) -> Result<Value, RpcFault> {
        if request.jsonrpc != "2.0" {
            return Err(RpcFault::invalid("jsonrpc must be '2.0'"));
        }
        let permission = permission_for(&request.method).ok_or_else(|| RpcFault {
            code: -32601,
            message: "method not found".into(),
            data: None,
        })?;
        if !request.auth.permissions.contains(permission) {
            return Err(RpcFault::forbidden(permission));
        }

        match request.method.as_str() {
            "project/create" => self.project_create(parse(request.params)?),
            "project/open" => self.project_open(parse(request.params)?),
            "application/catalog" => Ok(application_catalog()),
            "application/resolve" | "application/options" => {
                application_resolve(parse(request.params)?)
            }
            "application/preview" => application_preview(parse(request.params)?),
            "application/apply" => self.application_apply(parse(request.params)?),
            "product/compile" => self.product_compile(parse(request.params)?),
            "product/read" => self.product_read(),
            "product/context" => self.product_context(parse(request.params)?),
            "selection/update" => self.selection_update(parse(request.params)?),
            "slot/read" => self.slot_read(parse(request.params)?),
            "variant/generate" => self.variant_generate(parse(request.params)?),
            "variant/preview" => self.variant_preview(parse(request.params)?),
            "variant/equip" => self.variant_equip(parse(request.params)?),
            "configuration/save" => self.configuration_save(parse(request.params)?),
            "configuration/commit" => self.configuration_commit(parse(request.params)?),
            "analysis/start" => self.analysis_start(parse(request.params)?),
            "propagation/run" => self.propagation_run(parse(request.params)?),
            "analysis/cancel" => self.job_cancel(parse(request.params)?, "analysis"),
            "generation/submit" => self.generation_submit(parse(request.params)?),
            "generation/cancel" => self.job_cancel(parse(request.params)?, "generation"),
            "evidence/read" => self.evidence_read(parse(request.params)?),
            "release/evaluate" => self.release_evaluate(parse(request.params)?),
            "branch/create" => self.branch_create(parse(request.params)?),
            "branch/read" => self.branch_read(parse(request.params)?),
            "branch/update" => self.branch_update(parse(request.params)?),
            "integration/create" => self.integration_create(parse(request.params)?),
            "integration/evaluate" => self.integration_evaluate(parse(request.params)?),
            "integration/approve" => self.integration_approve(parse(request.params)?),
            "agent/start" => self.agent_start(parse(request.params)?),
            "agent/progress" => self.agent_progress(parse(request.params)?),
            "agent/cancel" => self.agent_cancel(parse(request.params)?),
            "agent/recover" => self.agent_recover(parse(request.params)?),
            "workflow/approve" => self.workflow_approve(parse(request.params)?),
            "workflow/start" => self.workflow_start(parse(request.params)?),
            "workflow/read" => self.workflow_read(parse(request.params)?),
            "workflow/cancel" => self.workflow_cancel(parse(request.params)?),
            "workflow/resume" => self.workflow_resume(parse(request.params)?),
            _ => Err(RpcFault {
                code: -32601,
                message: "method not found".into(),
                data: None,
            }),
        }
    }

    fn project_create(&mut self, params: ProjectCreate) -> Result<Value, RpcFault> {
        let name = params.name.trim();
        if name.is_empty()
            || name.chars().count() > 200
            || name.contains('/')
            || name.contains('\\')
            || params.description.chars().count() > 2000
        {
            return Err(RpcFault::invalid(
                "product name must be a display name without path separators and fit workspace limits",
            ));
        }
        let root = PathBuf::from(&params.workspace_root);
        if !root.is_absolute()
            || root.extension().and_then(|value| value.to_str()) != Some("dsworkspace")
            || root.exists()
        {
            return Err(RpcFault::invalid(
                "workspace_root must be a new absolute .dsworkspace directory",
            ));
        }
        let parent = root
            .parent()
            .ok_or_else(|| RpcFault::invalid("workspace has no parent"))?;
        if !parent.is_dir() {
            return Err(RpcFault::invalid(
                "workspace parent directory does not exist",
            ));
        }

        fs::create_dir(&root).map_err(|e| RpcFault::io("cannot create workspace", e))?;
        let result = (|| {
            for directory in [
                "mechanical",
                "electronics",
                "configurations",
                "contracts",
                "evidence",
                "assets",
                "generated",
                "cache",
                "reference-form",
                "reference-form/assets",
                "reference-form/assets/sha256",
                "reference-form/revisions",
                "reference-form/previews",
            ] {
                fs::create_dir(root.join(directory))
                    .map_err(|e| RpcFault::io("cannot create workspace directory", e))?;
            }

            let workspace_id = Uuid::new_v4();
            let product_id = Uuid::new_v4();
            let enclosure_node = Uuid::new_v4();
            let electronics_node = Uuid::new_v4();
            let pcb_node = Uuid::new_v4();
            let configuration_id = Uuid::new_v4();
            let graph = ProductGraph {
                schema: "design-studio.product-graph/1".into(),
                product_id,
                revision: 1,
                nodes: vec![
                    ProductNode {
                        id: enclosure_node,
                        name: "Enclosure".into(),
                        assembly_path: "product/mechanical/enclosure".into(),
                        domain: "mechanical".into(),
                        authority: "freecad".into(),
                        source_ref: "DesignStudioEnclosure".into(),
                        requirements: vec!["MECH-FIT".into(), "ENV-SEAL".into()],
                    },
                    ProductNode {
                        id: electronics_node,
                        name: "Electronics".into(),
                        assembly_path: "product/electronics/schematic".into(),
                        domain: "electronic".into(),
                        authority: "electronics".into(),
                        source_ref: "electronics/product.dsproj".into(),
                        requirements: vec!["ELEC-POWER".into(), "ELEC-INTERFACE".into()],
                    },
                    ProductNode {
                        id: pcb_node,
                        name: "PCB assembly".into(),
                        assembly_path: "product/electronics/pcb".into(),
                        domain: "electronic".into(),
                        authority: "electronics".into(),
                        source_ref: "electronics/product.dsproj#pcb".into(),
                        requirements: vec!["PCB-FIT".into(), "PCB-DRC".into()],
                    },
                ],
                edges: vec![
                    ProductEdge {
                        from: enclosure_node,
                        to: pcb_node,
                        kind: "constrains".into(),
                    },
                    ProductEdge {
                        from: pcb_node,
                        to: electronics_node,
                        kind: "implements".into(),
                    },
                ],
                slots: vec![
                    Slot {
                        id: "slot.enclosure".into(),
                        name: "Enclosure".into(),
                        node_id: enclosure_node,
                        assembly_path: "product/mechanical/enclosure".into(),
                        allowed_change_class: "C2".into(),
                        protected_properties: vec!["interface_datums".into()],
                        customizable_properties: vec![
                            "dimensions".into(),
                            "wall".into(),
                            "finish".into(),
                        ],
                        required_evidence: vec![
                            "fit".into(),
                            "wall_thickness".into(),
                            "sealing".into(),
                        ],
                    },
                    Slot {
                        id: "slot.electronics".into(),
                        name: "Electronics".into(),
                        node_id: electronics_node,
                        assembly_path: "product/electronics/schematic".into(),
                        allowed_change_class: "C4".into(),
                        protected_properties: vec!["interfaces".into()],
                        customizable_properties: vec!["components".into(), "power_tree".into()],
                        required_evidence: vec![
                            "erc".into(),
                            "power".into(),
                            "component_binding".into(),
                        ],
                    },
                    Slot {
                        id: "slot.pcb".into(),
                        name: "PCB".into(),
                        node_id: pcb_node,
                        assembly_path: "product/electronics/pcb".into(),
                        allowed_change_class: "C3".into(),
                        protected_properties: vec![
                            "outline".into(),
                            "connectors".into(),
                            "mounting".into(),
                        ],
                        customizable_properties: vec![
                            "placement".into(),
                            "routing".into(),
                            "stackup".into(),
                        ],
                        required_evidence: vec!["drc".into(), "clearance".into(), "thermal".into()],
                    },
                ],
                variants: vec![
                    Variant {
                        id: "enclosure.unconfigured".into(),
                        slot_id: "slot.enclosure".into(),
                        name: "Unconfigured enclosure".into(),
                        change_class: "C2".into(),
                        parameters: Map::new(),
                        failed_gates: vec![],
                        incomplete_gates: vec!["fit".into(), "sealing".into()],
                    },
                    Variant {
                        id: "electronics.unconfigured".into(),
                        slot_id: "slot.electronics".into(),
                        name: "Unconfigured electronics".into(),
                        change_class: "C4".into(),
                        parameters: Map::new(),
                        failed_gates: vec![],
                        incomplete_gates: vec!["erc".into(), "component_binding".into()],
                    },
                    Variant {
                        id: "pcb.unconfigured".into(),
                        slot_id: "slot.pcb".into(),
                        name: "Unconfigured PCB".into(),
                        change_class: "C3".into(),
                        parameters: Map::new(),
                        failed_gates: vec![],
                        incomplete_gates: vec!["drc".into(), "clearance".into()],
                    },
                ],
            };
            atomic_json(&root.join("product-graph.json"), &graph)?;

            let equipped = BTreeMap::from([
                ("slot.enclosure".into(), "enclosure.unconfigured".into()),
                ("slot.electronics".into(), "electronics.unconfigured".into()),
                ("slot.pcb".into(), "pcb.unconfigured".into()),
            ]);
            let mut baseline = Configuration {
                schema: "design-studio.configuration/1".into(),
                configuration_id,
                revision: 1,
                parent_configuration_id: None,
                baseline_configuration_id: configuration_id,
                workspace_revision: 1,
                graph_revision: 1,
                state: ConfigurationState::Committed,
                equipped,
                parameter_overrides: Map::new(),
                requirement_profile: "industrial-concept".into(),
                created_utc: now(),
                digest: String::new(),
            };
            baseline.digest = configuration_digest(&baseline)?;
            atomic_json(&root.join("configurations/baseline.json"), &baseline)?;

            let manifest = WorkspaceManifest {
                schema: "design-studio.workspace/1".into(),
                workspace_id,
                revision: 1,
                product: ProductIdentity {
                    name: name.into(),
                    description: params.description,
                },
                documents: Documents {
                    mechanical: "mechanical/product.FCStd".into(),
                    electronics: "electronics/product.dsproj".into(),
                },
                product_graph: "product-graph.json".into(),
                baseline_configuration: "configurations/baseline.json".into(),
                contracts: vec![],
                evidence_directories: vec!["evidence".into()],
            };
            atomic_json(&root.join("manifest.json"), &manifest)?;
            Ok(json!({
                "workspace_id": workspace_id, "product_id": product_id,
                "baseline_configuration_id": configuration_id,
                "manifest_path": root.join("manifest.json"),
                "mechanical_path": root.join("mechanical/product.FCStd"),
                "electronics_path": root.join("electronics/product.dsproj")
            }))
        })();
        if result.is_err() {
            let _ = fs::remove_dir_all(&root);
        }
        result
    }

    fn application_apply(&mut self, params: ApplicationApply) -> Result<Value, RpcFault> {
        if !params.approved {
            return Err(RpcFault::state(
                "applying a product option requires explicit approval",
            ));
        }
        let configuration_id = params
            .configuration_id
            .ok_or_else(|| RpcFault::invalid("configuration_id is required after approval"))?;
        let parent = self.configuration(configuration_id)?.clone();
        let resolved = resolve_application(&params.prompt, &params.answers)?;
        let option = resolved
            .options
            .iter()
            .find(|option| option.id == params.option_id)
            .ok_or_else(|| RpcFault::invalid("option_id is not one of the generated options"))?;
        let mut overrides = Map::new();
        overrides.insert("application_family".into(), json!(resolved.family.clone()));
        overrides.insert("application_option".into(), json!(option.id));
        overrides.insert(
            "captured_requirements".into(),
            json!(resolved.captured_requirements.clone()),
        );
        if resolved.family == "robotic_joint_capstone" {
            overrides.insert(
                "axis_requirements".into(),
                json!(resolved.axis_requirements.clone()),
            );
            overrides.insert(
                "system_architecture".into(),
                json!(resolved.system_architecture.clone()),
            );
        }
        overrides.insert(
            "stage_requirements".into(),
            json!({
                "mechanical": ["enclosure and mounting fit", "thermal clearance"],
                "schematic": ["power tree", "interfaces", "protection"],
                "pcb": ["placement and routing", "clearance and thermal"],
                "component_binding": ["exact part and footprint evidence"],
                "verification": option.unresolved_gates.clone(),
            }),
        );
        let mut board_configurations = Vec::new();
        let mut distributed_product_graph = Value::Null;
        if resolved.family == "robotic_joint_capstone" {
            let mut coordinator_overrides = Map::new();
            coordinator_overrides.insert("board_id".into(), json!("coordinator"));
            coordinator_overrides
                .insert("board_role".into(), json!("central_can_estop_coordinator"));
            coordinator_overrides.insert(
                "system_architecture".into(),
                json!(resolved.system_architecture.clone()),
            );
            let coordinator = self.new_configuration(
                &parent,
                parent.equipped.clone(),
                coordinator_overrides,
                "robot-board:coordinator".into(),
                ConfigurationState::Sandbox,
            )?;
            board_configurations.push(json!({
                "board_id": "coordinator", "role": "central_can_estop_coordinator",
                "configuration_id": coordinator.configuration_id,
                "configuration_digest": coordinator.digest,
            }));
            for (index, axis) in resolved.axis_requirements.iter().enumerate() {
                let mut axis_overrides = Map::new();
                axis_overrides.insert("board_id".into(), json!(format!("joint-{}", index + 1)));
                axis_overrides.insert("board_role".into(), json!("isolated_joint_controller"));
                axis_overrides.insert("axis_requirement".into(), axis.clone());
                let axis_child = self.new_configuration(
                    &parent,
                    parent.equipped.clone(),
                    axis_overrides,
                    format!("robot-board:joint-{}", index + 1),
                    ConfigurationState::Sandbox,
                )?;
                board_configurations.push(json!({
                    "board_id": format!("joint-{}", index + 1),
                    "axis_id": format!("axis-{}", index + 1),
                    "role": "isolated_joint_controller",
                    "configuration_id": axis_child.configuration_id,
                    "configuration_digest": axis_child.digest,
                    "requirements_digest": axis["requirements_digest"],
                }));
            }
            let graph = self.graph()?;
            let coordinator_id = board_configurations[0]["configuration_id"].clone();
            let mut nodes = Vec::new();
            let mut edges = Vec::new();
            for (index, board) in board_configurations.iter().enumerate() {
                let joint = index > 0;
                nodes.push(json!({
                    "id": board["configuration_id"],
                    "name": if joint { format!("Joint controller {}", index) }
                            else { "CAN and E-stop coordinator".into() },
                    "assembly_path": if joint { format!("robot/electronics/joint-{}", index) }
                                     else { "robot/electronics/coordinator".into() },
                    "domain": "electronic", "authority": "product_graph",
                    "source_ref": board["configuration_digest"],
                    "requirements": if joint { vec![format!("axis-{}", index)] }
                                    else { vec!["aggregate-power".into(), "CAN".into(),
                                                "E-stop".into(), "braking".into()] }
                }));
                if joint {
                    edges.push(json!({"from": board["configuration_id"],
                                      "to": coordinator_id, "kind": "depends_on"}));
                    edges.push(json!({"from": coordinator_id,
                                      "to": board["configuration_id"], "kind": "connects"}));
                }
            }
            distributed_product_graph = json!({
                "schema": "design-studio.product-graph/1", "product_id": graph.product_id,
                "revision": graph.revision, "nodes": nodes, "edges": edges,
                "slots": [], "variants": []
            });
            overrides.insert(
                "board_configurations".into(),
                json!(board_configurations.clone()),
            );
            overrides.insert(
                "distributed_product_graph".into(),
                distributed_product_graph.clone(),
            );
        }
        let child = self.new_configuration(
            &parent,
            parent.equipped.clone(),
            overrides,
            format!("application:{}", resolved.family),
            ConfigurationState::Sandbox,
        )?;
        Ok(json!({
            "configuration": child,
            "application": resolved,
            "board_configurations": board_configurations,
            "distributed_product_graph": distributed_product_graph,
            "approved": true,
            "baseline_unchanged": true,
            "authoritative_documents_changed": false,
            "stage_requirements": ["mechanical", "schematic", "pcb", "component_binding", "verification"]
        }))
    }

    fn project_open(&mut self, params: ProjectOpen) -> Result<Value, RpcFault> {
        let manifest_path = fs::canonicalize(&params.manifest_path)
            .map_err(|e| RpcFault::io("cannot open workspace manifest", e))?;
        let root = manifest_path
            .parent()
            .ok_or_else(|| RpcFault::invalid("manifest has no parent directory"))?
            .to_path_buf();
        let manifest: WorkspaceManifest = read_json(&manifest_path, "workspace manifest")?;
        validate_manifest(&manifest, &root)?;
        let graph_path = safe_join(&root, &manifest.product_graph)?;
        let graph: ProductGraph = read_json(&graph_path, "product graph")?;
        validate_graph(&graph)?;
        let baseline_path = safe_join(&root, &manifest.baseline_configuration)?;
        let baseline: Configuration = read_json(&baseline_path, "baseline configuration")?;
        validate_configuration(&baseline, &manifest, &graph)?;

        let state_path = root.join(".designstudio/state.json");
        let mut state = if state_path.exists() {
            read_json::<PersistedState>(&state_path, "agent state")?
        } else {
            PersistedState {
                schema: "design-studio.agent-state/1".into(),
                ..Default::default()
            }
        };
        if state.schema != "design-studio.agent-state/1"
            || state.configurations.values().any(|configuration| {
                configuration_digest(configuration)
                    .map(|digest| digest != configuration.digest)
                    .unwrap_or(true)
            })
        {
            return Err(RpcFault::state(
                "persisted agent state has an unsupported schema or a corrupt configuration",
            ));
        }
        for run in state.workflows.values() {
            let workflow_graph = state
                .workflow_graphs
                .get(&run.graph_digest)
                .ok_or_else(|| RpcFault::state("persisted workflow graph is unavailable"))?;
            validate_workflow_state(workflow_graph, run)?;
            if !state.product_intents.contains_key(&run.product_digest)
                || !state.interaction_maps.contains_key(&run.workflow_id)
            {
                return Err(RpcFault::state(
                    "persisted workflow inputs or interaction map are unavailable",
                ));
            }
            validate_product_intent(
                state
                    .product_intents
                    .get(&run.product_digest)
                    .expect("presence was checked"),
            )?;
            if digest_json(
                state
                    .product_intents
                    .get(&run.product_digest)
                    .expect("presence was checked"),
            )? != run.product_digest
            {
                return Err(RpcFault::state(
                    "persisted product intent digest is stale or corrupt",
                ));
            }
            if state.interaction_maps.get(&run.workflow_id) != Some(&controller_interaction_map()?)
            {
                return Err(RpcFault::state(
                    "persisted interaction surface map failed its schema invariants",
                ));
            }
        }
        if let Some(existing) = state.workspace_id {
            if existing != manifest.workspace_id || state.workspace_revision > manifest.revision {
                return Err(RpcFault::state(
                    "persisted state belongs to a different or newer workspace",
                ));
            }
        }
        state.workspace_id = Some(manifest.workspace_id);
        state.workspace_revision = manifest.revision;
        state
            .configurations
            .entry(baseline.configuration_id)
            .or_insert(baseline);
        state.evidence = load_evidence(&root, &manifest.evidence_directories)?;

        self.manifest_path = Some(manifest_path);
        self.workspace_root = Some(root);
        self.manifest = Some(manifest.clone());
        self.graph = Some(graph.clone());
        self.state = state;
        self.persist(
            "project/open",
            json!({"workspace_revision": manifest.revision}),
        )?;
        Ok(json!({
            "api": API_VERSION,
            "workspace_id": manifest.workspace_id,
            "workspace_revision": manifest.revision,
            "graph_revision": graph.revision,
            "daemon_revision": self.state.daemon_revision
        }))
    }

    fn product_read(&self) -> Result<Value, RpcFault> {
        let manifest = self.manifest()?;
        let graph = self.graph()?;
        let baseline = self
            .state
            .configurations
            .values()
            .find(|configuration| {
                configuration.configuration_id == configuration.baseline_configuration_id
                    && configuration.parent_configuration_id.is_none()
            })
            .ok_or_else(|| RpcFault::state("baseline configuration is unavailable"))?;
        Ok(json!({
            "workspace": manifest,
            "graph": graph,
            "selection": self.state.selection,
            "baseline_configuration": baseline
        }))
    }

    fn product_context(&self, params: ProductContext) -> Result<Value, RpcFault> {
        let graph = self.graph()?;
        if params.root_node_ids.is_empty() || params.max_nodes == 0 || params.max_nodes > 256 {
            return Err(RpcFault::invalid(
                "context requires roots and max_nodes in 1..256",
            ));
        }
        let all: BTreeSet<Uuid> = graph.nodes.iter().map(|node| node.id).collect();
        if params.root_node_ids.iter().any(|node| !all.contains(node)) {
            return Err(RpcFault::invalid(
                "context root does not exist in product graph",
            ));
        }
        let mut selected = BTreeSet::new();
        let mut frontier: BTreeSet<Uuid> = params.root_node_ids.iter().copied().collect();
        while selected.len() < params.max_nodes && !frontier.is_empty() {
            let node = *frontier.iter().next().expect("frontier is nonempty");
            frontier.remove(&node);
            if !selected.insert(node) {
                continue;
            }
            for edge in &graph.edges {
                if edge.from == node && !selected.contains(&edge.to) {
                    frontier.insert(edge.to);
                }
                if edge.to == node && !selected.contains(&edge.from) {
                    frontier.insert(edge.from);
                }
            }
        }
        let nodes: Vec<&ProductNode> = graph
            .nodes
            .iter()
            .filter(|node| selected.contains(&node.id))
            .collect();
        let edges: Vec<&ProductEdge> = graph
            .edges
            .iter()
            .filter(|edge| selected.contains(&edge.from) && selected.contains(&edge.to))
            .collect();
        let slots: Vec<&Slot> = graph
            .slots
            .iter()
            .filter(|slot| selected.contains(&slot.node_id))
            .collect();
        let slot_ids: BTreeSet<&str> = slots.iter().map(|slot| slot.id.as_str()).collect();
        let variants: Vec<&Variant> = graph
            .variants
            .iter()
            .filter(|variant| slot_ids.contains(variant.slot_id.as_str()))
            .collect();
        Ok(json!({
            "graph_revision": graph.revision,
            "root_node_ids": params.root_node_ids,
            "truncated": selected.len() == params.max_nodes && selected.len() < all.len(),
            "nodes": nodes, "edges": edges, "slots": slots, "variants": variants
        }))
    }

    fn selection_update(&mut self, params: SelectionUpdate) -> Result<Value, RpcFault> {
        let graph = self.graph()?;
        if !graph.nodes.iter().any(|node| node.id == params.node_id) {
            return Err(RpcFault::invalid(
                "selection node does not exist in product graph",
            ));
        }
        self.state.selection = Some(params.node_id);
        self.persist("selection/update", json!({"node_id": params.node_id}))?;
        Ok(json!({"node_id": params.node_id, "daemon_revision": self.state.daemon_revision}))
    }

    fn slot_read(&self, params: SlotRead) -> Result<Value, RpcFault> {
        let graph = self.graph()?;
        let slot = graph
            .slots
            .iter()
            .find(|slot| slot.id == params.slot_id)
            .ok_or_else(|| RpcFault::invalid("slot does not exist"))?;
        let variants: Vec<&Variant> = graph
            .variants
            .iter()
            .filter(|item| item.slot_id == slot.id)
            .collect();
        Ok(json!({"slot": slot, "variants": variants}))
    }

    fn variant_generate(&self, params: VariantGenerate) -> Result<Value, RpcFault> {
        self.configuration(params.configuration_id)?;
        let graph = self.graph()?;
        let slot = graph
            .slots
            .iter()
            .find(|slot| slot.id == params.slot_id)
            .ok_or_else(|| RpcFault::invalid("slot does not exist"))?;
        let candidates: Vec<&Variant> = graph
            .variants
            .iter()
            .filter(|variant| variant.slot_id == slot.id && variant.failed_gates.is_empty())
            .collect();
        Ok(json!({"slot_id": slot.id, "objective": params.objective, "candidates": candidates}))
    }

    fn variant_preview(&self, params: VariantAction) -> Result<Value, RpcFault> {
        let config = self.configuration(params.configuration_id)?;
        let (_, variant) = self.slot_variant(&params.slot_id, &params.variant_id)?;
        let mut equipped = config.equipped.clone();
        equipped.insert(params.slot_id, params.variant_id);
        Ok(json!({
            "preview": true,
            "base_configuration_id": config.configuration_id,
            "equipped": equipped,
            "failed_gates": variant.failed_gates,
            "incomplete_gates": variant.incomplete_gates,
            "persisted": false
        }))
    }

    fn variant_equip(&mut self, params: VariantAction) -> Result<Value, RpcFault> {
        let parent = self.configuration(params.configuration_id)?.clone();
        if parent.state == ConfigurationState::Released {
            return Err(RpcFault::state(
                "released configurations cannot be equipped; select a committed ancestor",
            ));
        }
        self.slot_variant(&params.slot_id, &params.variant_id)?;
        let slot_id = params.slot_id.clone();
        let mut equipped = parent.equipped.clone();
        equipped.insert(slot_id, params.variant_id);
        let child = self.new_configuration(
            &parent,
            equipped,
            parent.parameter_overrides.clone(),
            parent.requirement_profile.clone(),
            ConfigurationState::Sandbox,
        )?;
        Ok(json!({"configuration": child, "slot_id": params.slot_id, "parent_unchanged": true}))
    }

    fn configuration_save(&mut self, params: ConfigurationSave) -> Result<Value, RpcFault> {
        let parent = self.configuration(params.parent_configuration_id)?.clone();
        for (slot, variant) in &params.equipped {
            self.slot_variant(slot, variant)?;
        }
        let config = self.new_configuration(
            &parent,
            params.equipped,
            params.parameter_overrides,
            params.requirement_profile,
            ConfigurationState::Sandbox,
        )?;
        Ok(json!({"configuration": config, "parent_unchanged": true}))
    }

    fn configuration_commit(&mut self, params: ConfigurationId) -> Result<Value, RpcFault> {
        let parent = self.configuration(params.configuration_id)?.clone();
        if parent.state != ConfigurationState::Sandbox {
            return Err(RpcFault::state(
                "only a sandbox configuration can be committed",
            ));
        }
        let config = self.new_configuration(
            &parent,
            parent.equipped.clone(),
            parent.parameter_overrides.clone(),
            parent.requirement_profile.clone(),
            ConfigurationState::Committed,
        )?;
        Ok(json!({"configuration": config, "sandbox_unchanged": true}))
    }

    fn analysis_start(&mut self, params: AnalysisStart) -> Result<Value, RpcFault> {
        self.configuration(params.configuration_id)?;
        let job = Job {
            job_id: Uuid::new_v4(),
            kind: format!("analysis:{}", params.analysis_kind),
            configuration_id: params.configuration_id,
            slot_id: None,
            status: JobStatus::Queued,
            input_digest: digest_json(&params.inputs)?,
            created_utc: now(),
        };
        self.state.jobs.insert(job.job_id, job.clone());
        self.persist("analysis/start", json!({"job_id": job.job_id}))?;
        Ok(json!({"job": job}))
    }

    /// Run the coupled physical-intelligence gates synchronously over the
    /// affected product-graph subgraph.  This is deliberately a typed,
    /// deterministic screen: a missing exact solver remains `incomplete` and
    /// is never promoted to a pass.  Native geometry/electrical solvers can
    /// later provide the per-gate input records without changing this receipt.
    fn propagation_run(&mut self, params: PropagationStart) -> Result<Value, RpcFault> {
        self.configuration(params.configuration_id)?;
        if params.changed_node_ids.is_empty() || params.changed_node_ids.len() > 128 {
            return Err(RpcFault::invalid(
                "changed_node_ids must contain between 1 and 128 nodes",
            ));
        }
        let graph = self.graph()?.clone();
        let known: BTreeSet<Uuid> = graph.nodes.iter().map(|node| node.id).collect();
        let unique: BTreeSet<Uuid> = params.changed_node_ids.iter().copied().collect();
        if unique.len() != params.changed_node_ids.len() {
            return Err(RpcFault::invalid("changed_node_ids must be unique"));
        }
        if unique.iter().any(|node| !known.contains(node)) {
            return Err(RpcFault::invalid(
                "changed_node_ids contains an unknown product node",
            ));
        }

        let mut affected = unique.clone();
        let mut paths: BTreeMap<Uuid, Vec<Uuid>> =
            unique.iter().map(|node| (*node, vec![*node])).collect();
        let mut queue: VecDeque<Uuid> = unique.iter().copied().collect();
        while let Some(source) = queue.pop_front() {
            let source_path = paths.get(&source).cloned().unwrap_or_else(|| vec![source]);
            for edge in graph.edges.iter().filter(|edge| edge.from == source) {
                if affected.insert(edge.to) {
                    let mut path = source_path.clone();
                    path.push(edge.to);
                    paths.insert(edge.to, path);
                    queue.push_back(edge.to);
                }
            }
        }

        let affected_objects: Vec<Value> = graph
            .nodes
            .iter()
            .filter(|node| affected.contains(&node.id))
            .map(|node| {
                json!({
                    "node_id": node.id,
                    "name": node.name,
                    "assembly_path": node.assembly_path,
                    "domain": node.domain,
                    "authority": node.authority,
                    "source_ref": node.source_ref
                })
            })
            .collect();

        let mut evidence = Vec::new();
        let mut executed = Vec::new();
        let mut unaffected = Vec::new();
        let mut counts = BTreeMap::from([
            ("pass", 0_i64),
            ("fail", 0_i64),
            ("incomplete", 0_i64),
            ("not_applicable", 0_i64),
        ]);
        let run_id = Uuid::new_v4();
        let edit_reason = if params.edit_reason.trim().is_empty() {
            "user edit".to_string()
        } else {
            params.edit_reason.trim().to_string()
        };
        let specs: [(&str, &str, &[&str]); 8] = [
            (
                "reach_grip_viewing",
                "ergonomics",
                &["reach", "grip", "view", "control"],
            ),
            (
                "collision_tolerance_assembly_service",
                "mechanical",
                &["collision", "tolerance", "assembly", "service"],
            ),
            (
                "wall_draft_overhang_process",
                "manufacturing",
                &["wall", "draft", "overhang", "manufacturing", "process"],
            ),
            (
                "thermal_hot_surface",
                "thermal",
                &["thermal", "cooling", "hot"],
            ),
            (
                "structural_stiffness_vibration",
                "structural",
                &["structural", "load", "stiffness", "vibration"],
            ),
            (
                "cable_flex_connector",
                "routing",
                &["cable", "flex", "connector", "harness"],
            ),
            (
                "electrical_clearance_antenna_ground_emi",
                "electrical",
                &["electrical", "antenna", "ground", "emi", "pcb"],
            ),
            (
                "mass_center_stability",
                "stability",
                &["mass", "center", "centre", "stability", "balance"],
            ),
        ];
        for (gate_name, domain, keywords) in specs {
            let dependencies: Vec<Uuid> = graph
                .nodes
                .iter()
                .filter(|node| {
                    if !affected.contains(&node.id) {
                        return false;
                    }
                    let text = format!(
                        "{} {} {} {:?}",
                        node.name, node.assembly_path, node.domain, node.requirements
                    )
                    .to_lowercase();
                    keywords.iter().any(|keyword| text.contains(keyword))
                })
                .map(|node| node.id)
                .collect();
            let supplied = params.inputs.get(gate_name);
            let applicable = supplied.is_some() || !dependencies.is_empty();
            let dependency_ids = if dependencies.is_empty() {
                params.changed_node_ids.clone()
            } else {
                dependencies
            };
            let causal_paths: BTreeMap<String, Vec<Uuid>> = dependency_ids
                .iter()
                .filter_map(|node| paths.get(node).map(|path| (node.to_string(), path.clone())))
                .collect();
            let input_digest = digest_json(&json!({
                "configuration_id": params.configuration_id,
                "gate": gate_name,
                "input": supplied,
                "engine_version": params.engine_versions.get(domain)
                    .cloned().unwrap_or_else(|| "unavailable".to_string()),
            }))?;
            let (status, value, unit, requirement, margin, uncertainty, assumptions) =
                evaluate_propagation_gate(supplied, applicable)?;
            let status_name = evidence_status_name(&status);
            *counts.entry(status_name).or_insert(0) += 1;
            if applicable {
                executed.push(gate_name.to_string());
            } else {
                unaffected.push(gate_name.to_string());
            }
            let evidence_id = Uuid::new_v4();
            let created_utc = now();
            let rerun_reason = if applicable {
                format!(
                    "{} changed {} affected product node(s)",
                    edit_reason,
                    dependency_ids.len()
                )
            } else {
                "no affected node or supplied gate input".to_string()
            };
            let record = json!({
                "evidence_id": evidence_id,
                "configuration_id": params.configuration_id,
                "gate": gate_name,
                "domain": domain,
                "status": status_name,
                "method": "exact_solver_adapter",
                "source_run": format!("propagation:{}:{}", run_id, gate_name),
                "input_digest": input_digest,
                "value": value,
                "unit": unit,
                "requirement": requirement,
                "margin": margin,
                "uncertainty": uncertainty,
                "dependencies": dependency_ids,
                "causal_paths": causal_paths,
                "rerun_reason": rerun_reason,
                "assumptions": assumptions,
                "created_utc": created_utc,
            });
            // Persist the common evidence record as well as the richer v2
            // receipt so release/evidence readers continue to see the gate.
            self.state.evidence.push(EvidenceRecord {
                schema: "design-studio.evidence/1".into(),
                evidence_id,
                configuration_id: params.configuration_id,
                gate: gate_name.into(),
                status: status.clone(),
                method: "exact_solver_adapter".into(),
                source_run: format!("propagation:{}:{}", run_id, gate_name),
                input_digest: record["input_digest"].as_str().unwrap_or_default().into(),
                value: value.clone(),
                unit: unit.clone(),
                requirement: requirement.clone(),
                margin,
                uncertainty: uncertainty.clone(),
                assumptions: record["assumptions"]
                    .as_array()
                    .cloned()
                    .unwrap_or_default()
                    .iter()
                    .filter_map(|item| item.as_str().map(str::to_string))
                    .collect(),
                dependencies: dependency_ids.iter().map(ToString::to_string).collect(),
                created_utc: record["created_utc"].as_str().unwrap_or_default().into(),
            });
            evidence.push(record);
        }

        let score_changes = propagation_score_changes(params.inputs.get("scores"))?;
        let status = if counts["fail"] > 0 {
            "fail"
        } else if counts["incomplete"] > 0 {
            "incomplete"
        } else if counts["pass"] > 0 {
            "pass"
        } else {
            "not_applicable"
        };
        let result = json!({
            "schema": "design-studio.propagation-run/2",
            "run_id": run_id,
            "configuration_id": params.configuration_id,
            "graph_revision": graph.revision,
            "changed_node_ids": params.changed_node_ids,
            "affected_node_ids": affected,
            "causal_paths": paths,
            "affected_objects": affected_objects,
            "edit_reason": edit_reason,
            "executed_analyses": executed,
            "reused_analyses": [],
            "unaffected_analyses": unaffected,
            "score_changes": score_changes,
            "solver_summary": {
                "total": specs.len(), "pass": counts["pass"], "fail": counts["fail"],
                "incomplete": counts["incomplete"], "not_applicable": counts["not_applicable"]
            },
            "status": status,
            "evidence": evidence,
            "created_utc": now(),
        });
        self.persist(
            "propagation/run",
            json!({
                "run_id": run_id, "configuration_id": params.configuration_id,
                "changed_node_ids": params.changed_node_ids,
                "affected_node_ids": affected, "status": status
            }),
        )?;
        Ok(result)
    }

    fn generation_submit(&mut self, params: GenerationSubmit) -> Result<Value, RpcFault> {
        self.configuration(params.configuration_id)?;
        let graph = self.graph()?;
        if !graph.slots.iter().any(|slot| slot.id == params.slot_id) {
            return Err(RpcFault::invalid("slot does not exist"));
        }
        if params.candidate_count == 0
            || params.candidate_count > 16
            || params.seeds.len() != params.candidate_count as usize
        {
            return Err(RpcFault::invalid(
                "candidate_count must be 1..16 and equal the number of seeds",
            ));
        }
        let unique: BTreeSet<u64> = params.seeds.iter().copied().collect();
        if unique.len() != params.seeds.len() {
            return Err(RpcFault::invalid("generation seeds must be unique"));
        }
        let created_utc = now();
        let job = Job {
            job_id: Uuid::new_v4(),
            kind: "generation:hunyuan3d-omni-amd".into(),
            configuration_id: params.configuration_id,
            slot_id: Some(params.slot_id),
            status: JobStatus::Queued,
            input_digest: digest_json(&params.input_package)?,
            created_utc: created_utc.clone(),
        };
        self.state.jobs.insert(job.job_id, job.clone());
        self.persist("generation/submit", json!({"job_id": job.job_id}))?;
        Ok(json!({"job": {
            "schema": "design-studio.generation-job/1",
            "job_id": job.job_id,
            "configuration_id": job.configuration_id,
            "slot_id": job.slot_id,
            "provider": "hunyuan3d-omni-amd",
            "status": job.status,
            "input_digest": job.input_digest,
            "candidate_count": params.candidate_count,
            "seeds": params.seeds,
            "created_utc": created_utc,
            "artifact_manifest_url": null
        }}))
    }

    fn job_cancel(&mut self, params: JobId, expected: &str) -> Result<Value, RpcFault> {
        let job = self
            .state
            .jobs
            .get_mut(&params.job_id)
            .ok_or_else(|| RpcFault::invalid("job does not exist"))?;
        if !job.kind.starts_with(expected) {
            return Err(RpcFault::invalid(
                "job kind does not match cancellation method",
            ));
        }
        if !matches!(job.status, JobStatus::Queued | JobStatus::Running) {
            return Err(RpcFault::state(
                "only queued or running jobs can be cancelled",
            ));
        }
        job.status = JobStatus::Cancelled;
        let result = job.clone();
        self.persist(
            &format!("{expected}/cancel"),
            json!({"job_id": params.job_id}),
        )?;
        Ok(json!({"job": result}))
    }

    fn evidence_read(&self, params: ConfigurationId) -> Result<Value, RpcFault> {
        self.configuration(params.configuration_id)?;
        let records: Vec<&EvidenceRecord> = self
            .state
            .evidence
            .iter()
            .filter(|record| record.configuration_id == params.configuration_id)
            .collect();
        Ok(json!({"configuration_id": params.configuration_id, "records": records}))
    }

    fn release_evaluate(&self, params: ConfigurationId) -> Result<Value, RpcFault> {
        let config = self.configuration(params.configuration_id)?;
        if config.state != ConfigurationState::Committed {
            return Ok(release_result(
                "blocked",
                vec![gate(
                    "CONFIGURATION_STATE",
                    "configuration must be committed before release",
                )],
                vec![],
            ));
        }
        let graph = self.graph()?;
        let mut errors = Vec::new();
        let mut required = BTreeSet::new();
        for (slot_id, variant_id) in &config.equipped {
            let slot = graph
                .slots
                .iter()
                .find(|slot| &slot.id == slot_id)
                .ok_or_else(|| {
                    RpcFault::state(format!("configuration references missing slot {slot_id}"))
                })?;
            let variant = graph
                .variants
                .iter()
                .find(|variant| &variant.id == variant_id && variant.slot_id == *slot_id)
                .ok_or_else(|| {
                    RpcFault::state(format!(
                        "configuration references incompatible variant {variant_id}"
                    ))
                })?;
            required.extend(slot.required_evidence.iter().cloned());
            for item in &variant.failed_gates {
                errors.push(gate(
                    "VARIANT_FAILED_GATE",
                    &format!("{variant_id}: {item}"),
                ));
            }
            for item in &variant.incomplete_gates {
                errors.push(gate(
                    "VARIANT_INCOMPLETE_GATE",
                    &format!("{variant_id}: {item}"),
                ));
            }
        }
        for required_gate in required {
            let latest = self
                .state
                .evidence
                .iter()
                .filter(|record| {
                    record.configuration_id == config.configuration_id
                        && record.gate == required_gate
                })
                .max_by(|a, b| {
                    chrono::DateTime::parse_from_rfc3339(&a.created_utc)
                        .expect("evidence timestamps are validated on project open")
                        .cmp(
                            &chrono::DateTime::parse_from_rfc3339(&b.created_utc)
                                .expect("evidence timestamps are validated on project open"),
                        )
                });
            match latest.map(|record| &record.status) {
                Some(EvidenceStatus::Pass) => {}
                Some(EvidenceStatus::Fail) => errors.push(gate("EVIDENCE_FAILED", &required_gate)),
                Some(EvidenceStatus::Incomplete) => {
                    errors.push(gate("EVIDENCE_INCOMPLETE", &required_gate))
                }
                Some(EvidenceStatus::NotApplicable) => {
                    errors.push(gate("EVIDENCE_NOT_APPLICABLE", &required_gate))
                }
                None => errors.push(gate("EVIDENCE_MISSING", &required_gate)),
            }
        }
        let status = if errors.is_empty() {
            "ready"
        } else {
            "blocked"
        };
        Ok(release_result(status, errors, vec![]))
    }

    fn branch_create(&mut self, params: BranchCreate) -> Result<Value, RpcFault> {
        let baseline = self
            .configuration(params.baseline_configuration_id)?
            .clone();
        if !matches!(baseline.state, ConfigurationState::Committed) {
            return Err(RpcFault::state(
                "proposal branches require a committed baseline",
            ));
        }
        if params.name.trim().is_empty()
            || params.created_by.trim().is_empty()
            || !matches!(
                params.domain.as_str(),
                "industrial_design"
                    | "mechanical"
                    | "electrical"
                    | "pcb"
                    | "firmware"
                    | "manufacturing"
                    | "verification"
            )
            || params.requested_approvals.is_empty()
        {
            return Err(RpcFault::invalid(
                "branch name, author, supported domain and approvals are required",
            ));
        }
        validate_contract_revisions(&params.consumes_contracts)?;
        validate_contract_changes(&params.proposes_contract_changes)?;
        let root = self.root()?.to_path_buf();
        if params
            .artifacts
            .iter()
            .any(|path| safe_join(&root, path).is_err())
        {
            return Err(RpcFault::invalid(
                "branch artifact paths must remain inside the workspace",
            ));
        }
        let branch = ProposalBranch {
            schema: "design-studio.proposal-branch/1".into(),
            branch_id: Uuid::new_v4(),
            name: params.name,
            domain: params.domain,
            baseline_configuration_id: baseline.configuration_id,
            head_configuration_id: baseline.configuration_id,
            source_workspace_revision: baseline.workspace_revision,
            source_graph_revision: baseline.graph_revision,
            consumes_contracts: params.consumes_contracts,
            proposes_contract_changes: params.proposes_contract_changes,
            artifacts: params.artifacts,
            evidence_ids: params.evidence_ids,
            assumptions: params.assumptions,
            requested_approvals: params.requested_approvals,
            created_by: params.created_by,
            created_utc: now(),
        };
        self.state.branches.insert(branch.branch_id, branch.clone());
        self.persist("branch/create", json!({"branch_id": branch.branch_id}))?;
        Ok(json!({"branch": branch, "baseline_unchanged": true}))
    }

    fn branch_read(&self, params: BranchId) -> Result<Value, RpcFault> {
        let branch = self
            .state
            .branches
            .get(&params.branch_id)
            .ok_or_else(|| RpcFault::invalid("proposal branch does not exist"))?;
        let head = self.configuration(branch.head_configuration_id)?;
        Ok(json!({"branch": branch, "head_configuration": head}))
    }

    fn branch_update(&mut self, params: BranchUpdate) -> Result<Value, RpcFault> {
        let branch = self
            .state
            .branches
            .get(&params.branch_id)
            .cloned()
            .ok_or_else(|| RpcFault::invalid("proposal branch does not exist"))?;
        let parent = self.configuration(branch.head_configuration_id)?.clone();
        for (slot, variant) in &params.equipped {
            self.slot_variant(slot, variant)?;
        }
        let child = self.new_configuration(
            &parent,
            params.equipped,
            params.parameter_overrides,
            params.requirement_profile,
            ConfigurationState::Sandbox,
        )?;
        self.state
            .branches
            .get_mut(&params.branch_id)
            .expect("branch was checked")
            .head_configuration_id = child.configuration_id;
        self.persist(
            "branch/update",
            json!({
                "branch_id": params.branch_id,
                "head_configuration_id": child.configuration_id
            }),
        )?;
        Ok(
            json!({"branch_id": params.branch_id, "configuration": child,
                  "previous_head_unchanged": true, "baseline_unchanged": true}),
        )
    }

    fn integration_create(&mut self, params: IntegrationCreate) -> Result<Value, RpcFault> {
        self.configuration(params.baseline_configuration_id)?;
        if params.branch_ids.is_empty() || params.created_by.trim().is_empty() {
            return Err(RpcFault::invalid(
                "integration requires branches and a creator",
            ));
        }
        let unique: BTreeSet<Uuid> = params.branch_ids.iter().copied().collect();
        if unique.len() != params.branch_ids.len() {
            return Err(RpcFault::invalid("integration branch IDs must be unique"));
        }
        let mut branch_heads = BTreeMap::new();
        let mut required = BTreeSet::new();
        for branch_id in params.branch_ids {
            let branch = self
                .state
                .branches
                .get(&branch_id)
                .ok_or_else(|| RpcFault::invalid("integration branch does not exist"))?;
            if branch.baseline_configuration_id != params.baseline_configuration_id {
                return Err(RpcFault::state(
                    "integration branches must share the selected baseline",
                ));
            }
            branch_heads.insert(branch_id, branch.head_configuration_id);
            required.extend(branch.requested_approvals.iter().cloned());
        }
        let sandbox = IntegrationSandbox {
            schema: "design-studio.integration-sandbox/1".into(),
            integration_id: Uuid::new_v4(),
            baseline_configuration_id: params.baseline_configuration_id,
            branch_heads,
            status: IntegrationStatus::Draft,
            checks: vec![],
            required_approvals: required,
            approvals: BTreeMap::new(),
            created_by: params.created_by,
            created_utc: now(),
        };
        self.state
            .integrations
            .insert(sandbox.integration_id, sandbox.clone());
        self.persist(
            "integration/create",
            json!({"integration_id": sandbox.integration_id}),
        )?;
        Ok(json!({"integration": sandbox, "baseline_unchanged": true}))
    }

    fn integration_evaluate(&mut self, params: IntegrationId) -> Result<Value, RpcFault> {
        let snapshot = self
            .state
            .integrations
            .get(&params.integration_id)
            .cloned()
            .ok_or_else(|| RpcFault::invalid("integration sandbox does not exist"))?;
        let mut checks = Vec::new();
        let mut blocked = false;
        let mut contract_changes: BTreeMap<String, (u64, String, Uuid)> = BTreeMap::new();
        for (branch_id, head_id) in &snapshot.branch_heads {
            let branch = self
                .state
                .branches
                .get(branch_id)
                .ok_or_else(|| RpcFault::state("integration references a missing branch"))?;
            let current_head = branch.head_configuration_id;
            if current_head != *head_id {
                blocked = true;
                checks.push(gate(
                    "BRANCH_HEAD_STALE",
                    &format!("branch {branch_id} advanced after sandbox creation"),
                ));
            }
            if !self.configuration_descends_from(*head_id, snapshot.baseline_configuration_id)? {
                blocked = true;
                checks.push(gate(
                    "BRANCH_ANCESTRY",
                    &format!("branch {branch_id} does not descend from baseline"),
                ));
            }
            for evidence_id in &branch.evidence_ids {
                if !self
                    .state
                    .evidence
                    .iter()
                    .any(|record| record.evidence_id == *evidence_id)
                {
                    blocked = true;
                    checks.push(gate(
                        "BRANCH_EVIDENCE_MISSING",
                        &format!("branch {branch_id} evidence {evidence_id} is missing"),
                    ));
                }
            }
            for change in &branch.proposes_contract_changes {
                if let Some((version, digest, other)) = contract_changes.get(&change.contract_id) {
                    if *version != change.proposed_version || *digest != change.digest {
                        blocked = true;
                        checks.push(gate("CONTRACT_CONFLICT", &format!(
                            "branches {other} and {branch_id} propose incompatible {} revisions", change.contract_id)));
                    }
                } else {
                    contract_changes.insert(
                        change.contract_id.clone(),
                        (change.proposed_version, change.digest.clone(), *branch_id),
                    );
                }
            }
        }
        if !blocked {
            checks.push(json!({"code": "CROSS_DOMAIN_COMPATIBLE", "status": "pass",
                               "message": "branch ancestry, evidence and contract proposals are compatible"}));
        }
        let sandbox = self
            .state
            .integrations
            .get_mut(&params.integration_id)
            .expect("integration was checked");
        sandbox.status = if blocked {
            IntegrationStatus::Blocked
        } else {
            IntegrationStatus::ReadyForApproval
        };
        sandbox.checks = checks;
        sandbox.approvals.clear();
        let result = sandbox.clone();
        self.persist(
            "integration/evaluate",
            json!({
                "integration_id": params.integration_id,
                "status": result.status
            }),
        )?;
        Ok(json!({"integration": result, "baseline_unchanged": true}))
    }

    fn integration_approve(&mut self, params: IntegrationApprove) -> Result<Value, RpcFault> {
        let snapshot = self
            .state
            .integrations
            .get(&params.integration_id)
            .cloned()
            .ok_or_else(|| RpcFault::invalid("integration sandbox does not exist"))?;
        if !matches!(
            snapshot.status,
            IntegrationStatus::ReadyForApproval | IntegrationStatus::Approved
        ) {
            return Err(RpcFault::state(
                "integration must pass evaluation before approval",
            ));
        }
        if !snapshot.required_approvals.contains(&params.role) {
            return Err(RpcFault::invalid(
                "approval role was not requested by selected branches",
            ));
        }
        if params.approver.trim().is_empty()
            || snapshot.branch_heads.keys().any(|branch_id| {
                self.state
                    .branches
                    .get(branch_id)
                    .map(|branch| branch.created_by == params.approver)
                    .unwrap_or(false)
            })
        {
            return Err(RpcFault::forbidden("independent integration approver"));
        }
        let sandbox = self
            .state
            .integrations
            .get_mut(&params.integration_id)
            .expect("integration was checked");
        sandbox.approvals.insert(
            params.role.clone(),
            IntegrationApproval {
                role: params.role,
                approver: params.approver,
                created_utc: now(),
            },
        );
        if sandbox
            .required_approvals
            .iter()
            .all(|role| sandbox.approvals.contains_key(role))
        {
            sandbox.status = IntegrationStatus::Approved;
        }
        let result = sandbox.clone();
        self.persist(
            "integration/approve",
            json!({
                "integration_id": params.integration_id,
                "status": result.status
            }),
        )?;
        Ok(json!({"integration": result, "baseline_unchanged": true}))
    }

    fn agent_start(&mut self, params: AgentStart) -> Result<Value, RpcFault> {
        if !self.state.branches.contains_key(&params.branch_id)
            || params.agent_id.trim().is_empty()
            || params.kind.trim().is_empty()
            || params.budget.max_steps == 0
            || params.budget.max_steps > 100_000
            || params.budget.max_seconds == 0
            || params.budget.max_seconds > 604_800
            || params.budget.max_cost_microunits > 1_000_000_000
        {
            return Err(RpcFault::invalid(
                "agent task has an invalid branch, identity, kind or budget",
            ));
        }
        let task = AgentTask {
            schema: "design-studio.agent-task/1".into(),
            task_id: Uuid::new_v4(),
            branch_id: params.branch_id,
            agent_id: params.agent_id,
            kind: params.kind,
            status: AgentTaskStatus::Queued,
            budget: params.budget,
            consumed_steps: 0,
            consumed_seconds: 0,
            consumed_cost_microunits: 0,
            recovery_count: 0,
            created_utc: now(),
        };
        self.state.agent_tasks.insert(task.task_id, task.clone());
        self.persist("agent/start", json!({"task_id": task.task_id}))?;
        Ok(json!({"task": task}))
    }

    fn agent_progress(&mut self, params: AgentProgress) -> Result<Value, RpcFault> {
        let task = self
            .state
            .agent_tasks
            .get_mut(&params.task_id)
            .ok_or_else(|| RpcFault::invalid("agent task does not exist"))?;
        if !matches!(
            task.status,
            AgentTaskStatus::Queued | AgentTaskStatus::Running
        ) {
            return Err(RpcFault::state("only active agent tasks accept progress"));
        }
        if params.consumed_steps < task.consumed_steps
            || params.consumed_seconds < task.consumed_seconds
            || params.consumed_cost_microunits < task.consumed_cost_microunits
            || params.consumed_steps > task.budget.max_steps
            || params.consumed_seconds > task.budget.max_seconds
            || params.consumed_cost_microunits > task.budget.max_cost_microunits
        {
            return Err(RpcFault::state(
                "agent progress is non-monotonic or exceeds its budget",
            ));
        }
        task.consumed_steps = params.consumed_steps;
        task.consumed_seconds = params.consumed_seconds;
        task.consumed_cost_microunits = params.consumed_cost_microunits;
        task.status = if params.complete {
            AgentTaskStatus::Succeeded
        } else {
            AgentTaskStatus::Running
        };
        let result = task.clone();
        self.persist(
            "agent/progress",
            json!({"task_id": params.task_id,
                                               "status": result.status}),
        )?;
        Ok(json!({"task": result}))
    }

    fn agent_cancel(&mut self, params: AgentTaskId) -> Result<Value, RpcFault> {
        let task = self
            .state
            .agent_tasks
            .get_mut(&params.task_id)
            .ok_or_else(|| RpcFault::invalid("agent task does not exist"))?;
        if !matches!(
            task.status,
            AgentTaskStatus::Queued | AgentTaskStatus::Running
        ) {
            return Err(RpcFault::state("only active agent tasks can be cancelled"));
        }
        task.status = AgentTaskStatus::Cancelled;
        let result = task.clone();
        self.persist("agent/cancel", json!({"task_id": params.task_id}))?;
        Ok(json!({"task": result}))
    }

    fn agent_recover(&mut self, params: AgentTaskId) -> Result<Value, RpcFault> {
        let task = self
            .state
            .agent_tasks
            .get_mut(&params.task_id)
            .ok_or_else(|| RpcFault::invalid("agent task does not exist"))?;
        if !matches!(task.status, AgentTaskStatus::Running) || task.recovery_count >= 3 {
            return Err(RpcFault::state(
                "only interrupted running tasks with recovery budget can recover",
            ));
        }
        task.recovery_count += 1;
        task.status = AgentTaskStatus::Queued;
        let result = task.clone();
        self.persist(
            "agent/recover",
            json!({"task_id": params.task_id,
                                              "recovery_count": result.recovery_count}),
        )?;
        Ok(json!({"task": result}))
    }

    fn product_compile(&mut self, params: ProductCompile) -> Result<Value, RpcFault> {
        self.root()?;
        validate_schema(
            "product intent",
            include_str!("../../../docs/schemas/product-intent-v1.schema.json"),
            &params.intent,
        )?;
        validate_product_intent(&params.intent)?;
        validate_source_binding(&params.intent.source_product)?;
        let product_digest = digest_json(&params.intent)?;
        let packages = controller_work_packages();
        validate_package_order(&packages)?;
        let mut graph = WorkPackageGraph {
            schema: "design-studio.work-package-graph/1".into(),
            graph_id: String::new(),
            product_id: params.intent.product_id.clone(),
            product_digest: product_digest.clone(),
            runnable: false,
            approval_gate: "architecture".into(),
            packages,
            graph_digest: String::new(),
        };
        let graph_digest = digest_json(&graph)?;
        graph.graph_id = format!("wpg-{}", &graph_digest[..16]);
        graph.graph_digest = graph_digest.clone();
        let workflow_id = format!("wfr-{}", &graph_digest[..16]);
        let timestamp = now();
        let compiled_run = WorkflowRun {
            schema: "design-studio.workflow-run/1".into(),
            workflow_id: workflow_id.clone(),
            product_digest: product_digest.clone(),
            graph_digest: graph_digest.clone(),
            status: "awaiting_approval".into(),
            approval: None,
            checkpoints: graph
                .packages
                .iter()
                .map(|package| WorkflowCheckpoint {
                    package_id: package.id.clone(),
                    status: "blocked".into(),
                    input_digest: None,
                    output_digest: None,
                    result_ref: None,
                    consumed_units: 0,
                })
                .collect(),
            budget: WorkflowBudget {
                allocated_units: params.intent.budget_units,
                consumed_units: 0,
                remaining_units: params.intent.budget_units,
                rejected_packages: Vec::new(),
            },
            created_utc: timestamp.clone(),
            updated_utc: timestamp,
        };
        let run = self
            .state
            .workflows
            .get(&workflow_id)
            .cloned()
            .unwrap_or(compiled_run);
        let interaction_map = controller_interaction_map()?;
        validate_workflow_state(&graph, &run)?;
        validate_schema(
            "work-package graph",
            include_str!("../../../docs/schemas/work-package-graph-v1.schema.json"),
            &graph,
        )?;
        validate_schema(
            "workflow run",
            include_str!("../../../docs/schemas/workflow-run-v1.schema.json"),
            &run,
        )?;
        validate_schema_value(
            "interaction surface map",
            include_str!("../../../docs/schemas/interaction-surface-map-v1.schema.json"),
            &interaction_map,
        )?;
        self.state
            .product_intents
            .insert(product_digest.clone(), params.intent.clone());
        self.state
            .workflow_graphs
            .insert(graph_digest.clone(), graph.clone());
        self.state
            .interaction_maps
            .insert(workflow_id.clone(), interaction_map.clone());
        self.state
            .workflows
            .insert(workflow_id.clone(), run.clone());
        self.persist(
            "product/compile",
            json!({"workflow_id": workflow_id, "product_digest": product_digest,
                   "graph_digest": graph_digest}),
        )?;
        Ok(json!({
            "product_intent": params.intent,
            "product_digest": product_digest,
            "work_package_graph": graph,
            "workflow": run,
            "interaction_surface_map": interaction_map
        }))
    }

    fn workflow_approve(&mut self, params: WorkflowApprove) -> Result<Value, RpcFault> {
        let graph = self.workflow_inputs(
            &params.workflow_id,
            &params.product_digest,
            &params.graph_digest,
        )?;
        if params.approver.trim().is_empty() || params.approver.chars().count() > 120 {
            return Err(RpcFault::invalid("approver must be 1..120 characters"));
        }
        let run = self
            .state
            .workflows
            .get_mut(&params.workflow_id)
            .ok_or_else(|| RpcFault::invalid("workflow does not exist"))?;
        if run.status != "awaiting_approval" && run.status != "approved" {
            return Err(RpcFault::state(
                "architecture approval is no longer mutable",
            ));
        }
        let approval_digest = digest_json(&json!({
            "kind": "architecture",
            "approver": params.approver,
            "product_digest": params.product_digest,
            "graph_digest": params.graph_digest
        }))?;
        run.approval = Some(WorkflowApproval {
            kind: "architecture".into(),
            approver: params.approver,
            product_digest: params.product_digest,
            graph_digest: params.graph_digest,
            approval_digest,
            approved_utc: now(),
        });
        run.status = "approved".into();
        run.updated_utc = now();
        let result = run.clone();
        validate_workflow_state(&graph, &result)?;
        validate_schema(
            "workflow run",
            include_str!("../../../docs/schemas/workflow-run-v1.schema.json"),
            &result,
        )?;
        self.persist(
            "workflow/approve",
            json!({"workflow_id": params.workflow_id}),
        )?;
        Ok(json!({"workflow": result}))
    }

    fn workflow_start(&mut self, params: WorkflowAdvance) -> Result<Value, RpcFault> {
        self.advance_workflow(params, false)
    }

    fn workflow_resume(&mut self, params: WorkflowAdvance) -> Result<Value, RpcFault> {
        self.advance_workflow(params, true)
    }

    fn advance_workflow(
        &mut self,
        params: WorkflowAdvance,
        resume: bool,
    ) -> Result<Value, RpcFault> {
        let graph = self.workflow_inputs(
            &params.workflow_id,
            &params.product_digest,
            &params.graph_digest,
        )?;
        let mut run = self
            .state
            .workflows
            .get(&params.workflow_id)
            .cloned()
            .ok_or_else(|| RpcFault::invalid("workflow does not exist"))?;
        let approval = run.approval.as_ref().ok_or_else(|| {
            RpcFault::state("architecture approval is required before runnable work")
        })?;
        if approval.approval_digest != params.approval_digest {
            return Err(RpcFault::state("stale architecture approval digest"));
        }
        if resume {
            if run.status != "cancelled" && run.status != "running" {
                return Err(RpcFault::state(
                    "only a cancelled or incomplete workflow can resume",
                ));
            }
            for checkpoint in &mut run.checkpoints {
                if checkpoint.status == "cancelled" {
                    checkpoint.status = "blocked".into();
                }
            }
        } else if run.status != "approved" {
            return Err(RpcFault::state(
                "workflow/start requires an approved workflow",
            ));
        }
        run.status = "running".into();
        if params.package_limit.unwrap_or(1) != 1 {
            return Err(RpcFault::invalid(
                "package_limit must be 1; workflows advance one durable cooperative checkpoint per RPC",
            ));
        }
        run.updated_utc = now();
        validate_workflow_state(&graph, &run)?;
        self.state
            .workflows
            .insert(params.workflow_id.clone(), run.clone());
        let event = if resume {
            "workflow/resume"
        } else {
            "workflow/start"
        };
        self.persist(
            event,
            json!({"workflow_id": params.workflow_id, "status": "running"}),
        )?;

        let root = self.root()?.to_path_buf();
        let source_root = self
            .state
            .product_intents
            .get(&params.product_digest)
            .ok_or_else(|| RpcFault::state("workflow product intent is unavailable"))?
            .source_product
            .source_root
            .clone();
        let source_root = fs::canonicalize(source_root)
            .map_err(|error| RpcFault::io("cannot resolve workflow source root", error))?;
        let completed = execute_one_ready_package(&root, &source_root, &graph, &mut run)?;
        run.updated_utc = now();
        validate_workflow_state(&graph, &run)?;
        validate_schema(
            "workflow run",
            include_str!("../../../docs/schemas/workflow-run-v1.schema.json"),
            &run,
        )?;
        self.state
            .workflows
            .insert(params.workflow_id.clone(), run.clone());
        self.persist(
            "workflow/checkpoint",
            json!({"workflow_id": params.workflow_id, "package_id": completed,
                   "status": run.status}),
        )?;
        let result = run.clone();
        Ok(json!({"workflow": result}))
    }

    fn workflow_read(&self, params: WorkflowRead) -> Result<Value, RpcFault> {
        let run = self
            .state
            .workflows
            .get(&params.workflow_id)
            .ok_or_else(|| RpcFault::invalid("workflow does not exist"))?;
        let graph = self
            .state
            .workflow_graphs
            .get(&run.graph_digest)
            .ok_or_else(|| RpcFault::state("workflow graph is unavailable"))?;
        let intent = self
            .state
            .product_intents
            .get(&run.product_digest)
            .ok_or_else(|| RpcFault::state("workflow product intent is unavailable"))?;
        let map = self
            .state
            .interaction_maps
            .get(&params.workflow_id)
            .ok_or_else(|| RpcFault::state("workflow interaction map is unavailable"))?;
        validate_workflow_state(graph, run)?;
        validate_schema(
            "product intent",
            include_str!("../../../docs/schemas/product-intent-v1.schema.json"),
            intent,
        )?;
        validate_schema(
            "work-package graph",
            include_str!("../../../docs/schemas/work-package-graph-v1.schema.json"),
            graph,
        )?;
        validate_schema(
            "workflow run",
            include_str!("../../../docs/schemas/workflow-run-v1.schema.json"),
            run,
        )?;
        validate_schema_value(
            "interaction surface map",
            include_str!("../../../docs/schemas/interaction-surface-map-v1.schema.json"),
            map,
        )?;
        validate_engine_result_artifacts(self.root()?, run)?;
        Ok(
            json!({"product_intent": intent, "work_package_graph": graph,
                  "workflow": run, "interaction_surface_map": map}),
        )
    }

    fn workflow_cancel(&mut self, params: WorkflowCancel) -> Result<Value, RpcFault> {
        let graph = self.workflow_inputs(
            &params.workflow_id,
            &params.product_digest,
            &params.graph_digest,
        )?;
        let run = self
            .state
            .workflows
            .get_mut(&params.workflow_id)
            .ok_or_else(|| RpcFault::invalid("workflow does not exist"))?;
        if run.status != "approved" && run.status != "running" {
            return Err(RpcFault::state(
                "only approved or running workflow can be cancelled",
            ));
        }
        for checkpoint in &mut run.checkpoints {
            if checkpoint.status != "succeeded" && checkpoint.status != "rejected_over_budget" {
                checkpoint.status = "cancelled".into();
            }
        }
        run.status = "cancelled".into();
        run.updated_utc = now();
        validate_workflow_state(&graph, run)?;
        validate_schema(
            "workflow run",
            include_str!("../../../docs/schemas/workflow-run-v1.schema.json"),
            run,
        )?;
        let result = run.clone();
        self.persist(
            "workflow/cancel",
            json!({"workflow_id": params.workflow_id}),
        )?;
        Ok(json!({"workflow": result}))
    }

    fn workflow_inputs(
        &self,
        workflow_id: &str,
        product_digest: &str,
        graph_digest: &str,
    ) -> Result<WorkPackageGraph, RpcFault> {
        if !is_sha256(product_digest) || !is_sha256(graph_digest) {
            return Err(RpcFault::invalid("workflow digests must be SHA-256"));
        }
        let run = self
            .state
            .workflows
            .get(workflow_id)
            .ok_or_else(|| RpcFault::invalid("workflow does not exist"))?;
        if run.product_digest != product_digest || run.graph_digest != graph_digest {
            return Err(RpcFault::state(
                "stale product or work-package graph digest",
            ));
        }
        let intent = self
            .state
            .product_intents
            .get(product_digest)
            .ok_or_else(|| RpcFault::state("workflow product intent is unavailable"))?;
        validate_source_binding(&intent.source_product)
            .map_err(|_| RpcFault::state("stale source acceptance project digest"))?;
        self.state
            .workflow_graphs
            .get(graph_digest)
            .cloned()
            .ok_or_else(|| RpcFault::state("workflow graph is unavailable"))
    }

    fn configuration_descends_from(
        &self,
        mut child: Uuid,
        ancestor: Uuid,
    ) -> Result<bool, RpcFault> {
        let mut seen = BTreeSet::new();
        loop {
            if child == ancestor {
                return Ok(true);
            }
            if !seen.insert(child) {
                return Err(RpcFault::state("configuration ancestry contains a cycle"));
            }
            let config = self.configuration(child)?;
            match config.parent_configuration_id {
                Some(parent) => child = parent,
                None => return Ok(false),
            }
        }
    }

    fn new_configuration(
        &mut self,
        parent: &Configuration,
        equipped: BTreeMap<String, String>,
        parameter_overrides: Map<String, Value>,
        requirement_profile: String,
        state: ConfigurationState,
    ) -> Result<Configuration, RpcFault> {
        let manifest = self.manifest()?;
        let graph = self.graph()?;
        let mut child = Configuration {
            schema: "design-studio.configuration/1".into(),
            configuration_id: Uuid::new_v4(),
            revision: parent.revision + 1,
            parent_configuration_id: Some(parent.configuration_id),
            baseline_configuration_id: parent.baseline_configuration_id,
            workspace_revision: manifest.revision,
            graph_revision: graph.revision,
            state,
            equipped,
            parameter_overrides,
            requirement_profile,
            created_utc: now(),
            digest: String::new(),
        };
        child.digest = configuration_digest(&child)?;
        self.write_configuration(&child)?;
        self.state
            .configurations
            .insert(child.configuration_id, child.clone());
        self.persist("configuration/create", json!({"configuration_id": child.configuration_id, "parent_configuration_id": parent.configuration_id}))?;
        Ok(child)
    }

    fn write_configuration(&self, config: &Configuration) -> Result<(), RpcFault> {
        let root = self.root()?;
        let directory = ensure_local_directory(root, "configurations")?;
        let path = directory.join(format!("{}.json", config.configuration_id));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|e| {
                RpcFault::io(
                    "configuration snapshot already exists or cannot be created",
                    e,
                )
            })?;
        serde_json::to_writer_pretty(&mut file, config)
            .map_err(|e| RpcFault::io("cannot serialize configuration", e))?;
        file.write_all(b"\n")
            .map_err(|e| RpcFault::io("cannot finish configuration snapshot", e))?;
        file.sync_all()
            .map_err(|e| RpcFault::io("cannot sync configuration snapshot", e))
    }

    fn persist(&mut self, event: &str, details: Value) -> Result<(), RpcFault> {
        let root = self.root()?.to_path_buf();
        let directory = ensure_local_directory(&root, ".designstudio")?;
        self.state.daemon_revision += 1;
        atomic_json(&directory.join("state.json"), &self.state)?;
        let event_record = json!({
            "schema": "design-studio.agent-event/1",
            "sequence": self.state.daemon_revision,
            "event": event,
            "utc": now(),
            "details": details
        });
        let event_path = directory.join("events.jsonl");
        if event_path
            .symlink_metadata()
            .map(|metadata| metadata.file_type().is_symlink())
            .unwrap_or(false)
        {
            return Err(RpcFault::state("event log cannot be a symbolic link"));
        }
        let mut log = OpenOptions::new()
            .create(true)
            .append(true)
            .open(event_path)
            .map_err(|e| RpcFault::io("cannot append event log", e))?;
        serde_json::to_writer(&mut log, &event_record)
            .map_err(|e| RpcFault::io("cannot serialize event", e))?;
        log.write_all(b"\n")
            .map_err(|e| RpcFault::io("cannot append event", e))?;
        log.sync_data()
            .map_err(|e| RpcFault::io("cannot sync event log", e))
    }

    fn root(&self) -> Result<&Path, RpcFault> {
        self.workspace_root
            .as_deref()
            .ok_or_else(|| RpcFault::state("open a project first"))
    }

    fn manifest(&self) -> Result<&WorkspaceManifest, RpcFault> {
        self.manifest
            .as_ref()
            .ok_or_else(|| RpcFault::state("open a project first"))
    }

    fn graph(&self) -> Result<&ProductGraph, RpcFault> {
        self.graph
            .as_ref()
            .ok_or_else(|| RpcFault::state("open a project first"))
    }

    fn configuration(&self, id: Uuid) -> Result<&Configuration, RpcFault> {
        let configuration = self
            .state
            .configurations
            .get(&id)
            .ok_or_else(|| RpcFault::invalid("configuration does not exist"))?;
        if configuration.digest != configuration_digest(configuration)? {
            return Err(RpcFault::state("configuration digest mismatch"));
        }
        if let (Some(manifest), Some(graph)) = (&self.manifest, &self.graph) {
            if configuration.workspace_revision != manifest.revision
                || configuration.graph_revision != graph.revision
            {
                return Err(RpcFault::state(
                    "configuration is stale for the open workspace or product graph revision",
                ));
            }
        }
        Ok(configuration)
    }

    fn slot_variant(&self, slot_id: &str, variant_id: &str) -> Result<(&Slot, &Variant), RpcFault> {
        let graph = self.graph()?;
        let slot = graph
            .slots
            .iter()
            .find(|slot| slot.id == slot_id)
            .ok_or_else(|| RpcFault::invalid("slot does not exist"))?;
        let variant = graph
            .variants
            .iter()
            .find(|variant| variant.id == variant_id && variant.slot_id == slot.id)
            .ok_or_else(|| {
                RpcFault::invalid("variant does not exist or is incompatible with slot")
            })?;
        Ok((slot, variant))
    }
}

fn permission_for(method: &str) -> Option<&'static str> {
    match method {
        "project/create" => Some("project:create"),
        "project/open" => Some("project:open"),
        "application/catalog"
        | "application/resolve"
        | "application/options"
        | "application/preview" => Some("application:read"),
        "application/apply" => Some("configuration:write"),
        "product/compile" => Some("workflow:compile"),
        "product/read" | "product/context" | "slot/read" | "evidence/read" => Some("product:read"),
        "selection/update" => Some("selection:write"),
        "variant/generate" | "variant/preview" => Some("variant:read"),
        "variant/equip" | "configuration/save" | "configuration/commit" => {
            Some("configuration:write")
        }
        "analysis/start" | "analysis/cancel" | "propagation/run" => Some("analysis:execute"),
        "generation/submit" | "generation/cancel" => Some("generation:execute"),
        "release/evaluate" => Some("release:evaluate"),
        "branch/read" => Some("branch:read"),
        "branch/create" | "branch/update" => Some("branch:write"),
        "integration/create" | "integration/evaluate" => Some("integration:write"),
        "integration/approve" => Some("integration:approve"),
        "agent/start" | "agent/progress" | "agent/cancel" | "agent/recover" => Some("agent:manage"),
        "workflow/read" => Some("workflow:read"),
        "workflow/approve" => Some("workflow:approve"),
        "workflow/start" | "workflow/cancel" | "workflow/resume" => Some("workflow:execute"),
        _ => None,
    }
}

fn application_catalog() -> Value {
    let families: Vec<ApplicationCatalogEntry> = application_family_ids()
        .into_iter()
        .map(|family| application_entry(family))
        .collect();
    json!({
        "schema": "design-studio.application-catalog/1",
        "families": families,
        "custom_concepts_supported": true,
        "default_option_count": 3,
        "maximum_next_questions": 3
    })
}

fn application_family_ids() -> Vec<&'static str> {
    vec![
        "synchronous_buck_converter",
        "precision_acquisition_board",
        "wireless_sensor_2_4ghz",
        "usb_gigabit_high_speed_board",
        "bldc_servo_controller",
        "industrial_condition_monitor",
        "robotic_joint_capstone",
        "desktop_ai_control_console",
    ]
}

fn application_entry(family: &str) -> ApplicationCatalogEntry {
    let (name, starter_label, summary, ranked_decisions, _) = application_family(family);
    ApplicationCatalogEntry {
        family: family.into(),
        name: name.into(),
        starter_label: starter_label.into(),
        summary: summary.into(),
        ranked_decisions: ranked_decisions.into_iter().map(String::from).collect(),
    }
}

fn application_family(
    family: &str,
) -> (
    &'static str,
    &'static str,
    &'static str,
    Vec<&'static str>,
    Vec<&'static str>,
) {
    match family {
        "synchronous_buck_converter" => (
            "Synchronous buck converter",
            "Synchronous buck converter",
            "Efficiently reduce one DC voltage to another.",
            vec!["input supply", "required output", "maximum load"],
            vec!["input_supply", "output_requirement", "maximum_load"],
        ),
        "precision_acquisition_board" => (
            "Precision acquisition board",
            "Precision acquisition board",
            "Measure small sensor or analog signals accurately.",
            vec!["sensor or signal type", "required sensitivity", "operating environment"],
            vec!["signal_type", "sensitivity", "environment"],
        ),
        "wireless_sensor_2_4ghz" => (
            "2.4 GHz wireless sensor",
            "2.4 GHz wireless sensor",
            "Sense a physical condition and send the readings wirelessly.",
            vec!["sensing purpose", "range and location", "power source or battery-life goal"],
            vec!["sensing_purpose", "range_location", "power_goal"],
        ),
        "usb_gigabit_high_speed_board" => (
            "USB/Gigabit high-speed board",
            "USB/Gigabit high-speed board",
            "Move data reliably over USB or Gigabit-class links.",
            vec!["interface and speed", "cable and connector arrangement", "board constraints"],
            vec!["interface_speed", "cable_connectors", "board_constraints"],
        ),
        "bldc_servo_controller" => (
            "BLDC servo controller",
            "BLDC servo controller",
            "Control a brushless motor with smooth motion and feedback.",
            vec!["supply", "motor load and current", "smoothness and position feedback"],
            vec!["supply", "motor_load_current", "smoothness_feedback"],
        ),
        "industrial_condition_monitor" => (
            "Industrial condition monitor",
            "Industrial condition monitor",
            "Watch equipment health with sensors and an industrial connection.",
            vec!["monitored equipment", "sensors", "industrial connection and environment"],
            vec!["equipment", "sensors", "industrial_environment"],
        ),
        "robotic_joint_capstone" => (
            "Distributed six-axis robot electronics",
            "Six-axis robotic arm electronics",
            "Coordinate six independently sized isolated joint controllers over CAN with a central E-stop coordinator.",
            vec!["six-axis motor and encoder table", "supply, CAN topology, and cables", "E-stop, braking, environment, and safety"],
            vec!["axis_requirements", "supply_can_topology", "safety_environment"],
        ),
        "desktop_ai_control_console" => (
            "Desktop AI control console",
            "USB-C plus BLE desktop control console",
            "A local, non-GPU desktop controller derived from the accepted agent workflow controller.",
            vec!["USB-C role and power", "BLE interaction", "local execution budget"],
            vec!["usb_c_role", "ble_interaction", "local_budget"],
        ),
        _ => (
            "Custom product concept",
            "Custom product concept",
            "A product concept that needs a short engineering discovery pass.",
            vec!["what it must do", "where it will operate", "what matters most"],
            vec!["purpose", "environment", "priority"],
        ),
    }
}

fn application_keywords(family: &str) -> Vec<&'static str> {
    match family {
        "synchronous_buck_converter" => {
            vec!["buck", "step-down", "voltage converter", "power supply"]
        }
        "precision_acquisition_board" => vec![
            "precision acquisition",
            "data acquisition",
            "adc",
            "analog measurement",
        ],
        "wireless_sensor_2_4ghz" => vec![
            "wireless sensor",
            "temperature sensor",
            "ble sensor",
            "2.4 ghz",
            "wifi sensor",
        ],
        "usb_gigabit_high_speed_board" => vec![
            "usb",
            "gigabit",
            "high-speed board",
            "ethernet board",
            "10g",
        ],
        "bldc_servo_controller" => vec![
            "bldc",
            "brushless",
            "servo controller",
            "motor controller",
            "foc",
        ],
        "industrial_condition_monitor" => vec![
            "condition monitor",
            "machine health",
            "vibration monitor",
            "industrial monitor",
        ],
        "robotic_joint_capstone" => vec![
            "robot",
            "robotic joint",
            "robot arm",
            "robotic arm",
            "joint controller",
            "actuator",
        ],
        "desktop_ai_control_console" => vec![
            "desktop ai control console",
            "desktop control console",
            "usb-c plus ble",
            "usb c and ble",
            "ai console",
        ],
        _ => vec![],
    }
}

fn application_questions(family: &str) -> Vec<ApplicationQuestion> {
    let (_, _, _, _, ids) = application_family(family);
    let prompts: Vec<(&str, &str, &str, Vec<&str>)> = match family {
        "synchronous_buck_converter" => vec![
            ("input_supply", "What voltage will power it?", "This sets the safe voltage range and input protection.", vec!["5 V", "12 V", "24 V"]),
            ("output_requirement", "What voltage should it produce?", "This sets the power-conversion ratio and regulation target.", vec!["3.3 V", "5 V", "Other"]),
            ("maximum_load", "How much current must it deliver at most?", "This changes component size, heat, and board cost.", vec!["Under 1 A", "1–5 A", "Over 5 A"]),
        ],
        "precision_acquisition_board" => vec![
            ("signal_type", "What kind of signal should it measure?", "Voltage, current, bridge, and thermocouple signals need different input paths.", vec!["Sensor voltage", "Bridge or strain", "Thermocouple"]),
            ("sensitivity", "How small a change must it notice?", "This determines amplifier and converter precision.", vec!["Good enough for trends", "Fine measurement", "Very small changes"]),
            ("environment", "Where will it operate?", "Noise, temperature, and isolation needs depend on the setting.", vec!["Bench", "Factory", "Outdoor or harsh"]),
        ],
        "wireless_sensor_2_4ghz" => vec![
            ("sensing_purpose", "What should it sense?", "The sensor and calibration path depend on the physical measurement.", vec!["Temperature", "Motion or vibration", "Other"]),
            ("range_location", "How far away will the receiver be?", "Range and walls change the antenna and radio choices.", vec!["Same room", "Across a building", "Outdoor"]),
            ("power_goal", "What powers it, and how long should it last?", "Battery life changes radio duty cycle, size, and sleep strategy.", vec!["USB or mains", "Months on battery", "Years on battery"]),
        ],
        "usb_gigabit_high_speed_board" => vec![
            ("interface_speed", "Which data connection and speed do you need?", "This determines the signal-quality and processing requirements.", vec!["USB 2", "USB 3", "Gigabit Ethernet"]),
            ("cable_connectors", "What cables and connectors will be used?", "Connector choice affects signal loss, durability, and enclosure fit.", vec!["Short internal cable", "USB-C", "Long Ethernet cable"]),
            ("board_constraints", "How small or constrained is the board?", "Size and layer count affect routing difficulty and cost.", vec!["Prototype board", "Small enclosure", "Very compact"]),
        ],
        "bldc_servo_controller" => vec![
            ("supply", "What supply voltage is available?", "Voltage sets motor-driver and protection ratings.", vec!["12 V", "24 V", "48 V"]),
            ("motor_load_current", "How hard will the motor work?", "Peak current drives heat, copper, and driver size.", vec!["Small", "Moderate", "Heavy load"]),
            ("smoothness_feedback", "How smooth and precise must motion be?", "Feedback choice changes sensors, control software, and cost.", vec!["Simple motion", "Smooth motion", "Precise positioning"]),
        ],
        "industrial_condition_monitor" => vec![
            ("equipment", "What equipment should it watch?", "The failure modes determine which sensors are useful.", vec!["Motor or pump", "Gearbox", "General machine"]),
            ("sensors", "Which changes matter most?", "Sensor count and bandwidth affect power, cost, and enclosure size.", vec!["Temperature", "Vibration", "Temperature and vibration"]),
            ("industrial_environment", "What connection and environment should it handle?", "Isolation, sealing, and protection depend on the installation.", vec!["Bench", "24 V industrial", "Outdoor or wet"]),
        ],
        "robotic_joint_capstone" => vec![
            ("axis_requirements", "Provide one row for each of the six axes: motor type/voltage, continuous and peak current, encoder, brake, and thermal limits.", "Each joint board is sized independently; one axis must never inherit another axis's current, encoder, brake, or temperature assumptions.", vec!["Six-row requirements table", "Use explicit provisional rows", "Import a requirements file"]),
            ("supply_can_topology", "What are the supply range, CAN topology/bit rate/termination, and cable lengths and ratings?", "These values set aggregate bus current, voltage drop, connector/cable ratings, CAN loading, and termination.", vec!["Daisy-chain CAN", "Trunk with short stubs", "Provide a wiring diagram"]),
            ("safety_environment", "What E-stop category, brake/regeneration behavior, environment, and safety expectations apply?", "Safe-torque-off propagation, braking energy, temperature, ingress, and release evidence depend on these expectations.", vec!["Prototype only", "Industrial guarded cell", "Human-adjacent assessment"]),
        ],
        "desktop_ai_control_console" => vec![
            ("usb_c_role", "Which USB-C data and power role should the console use?", "The role controls connector protection, CC behavior, and power architecture.", vec!["USB 2 device", "Dual-role USB 2", "Powered host"]),
            ("ble_interaction", "Which controls and telemetry should BLE expose?", "The interaction contract determines radio, security, and firmware verification.", vec!["Status only", "Controls and status", "Provisioning and controls"]),
            ("local_budget", "What local workflow budget should be allocated?", "A fixed budget makes execution and resume accounting deterministic.", vec!["72 units", "100 units", "Custom bounded budget"]),
        ],
        _ => vec![
            ("purpose", "What must the product do?", "The main function determines the architecture.", vec!["Sense", "Control", "Communicate"]),
            ("environment", "Where will it operate?", "Environment changes protection, materials, and testing.", vec!["Bench", "Indoor product", "Industrial or outdoor"]),
            ("priority", "What matters most?", "The priority chooses the main trade-off.", vec!["Lower cost", "Balanced", "Performance or safety"]),
        ],
    };
    prompts
        .into_iter()
        .zip(ids)
        .map(|((_id, prompt, why, choices), key)| ApplicationQuestion {
            id: key.into(),
            prompt: prompt.into(),
            why_it_matters: why.into(),
            choices: choices.into_iter().map(String::from).collect(),
        })
        .collect()
}

fn classify_application(prompt: &str) -> (String, String, f64, bool) {
    let lower = prompt.to_lowercase();
    let mut best = (String::from("custom_concept"), 0usize);
    for family in application_family_ids() {
        let score = application_keywords(family)
            .iter()
            .filter(|term| lower.contains(**term))
            .count();
        if score > best.1 {
            best = (family.into(), score);
        }
    }
    if best.1 == 0 {
        return (best.0, "Custom product concept".into(), 0.25, true);
    }
    let (name, _, _, _, _) = application_family(&best.0);
    (
        best.0,
        name.into(),
        (0.62 + best.1 as f64 * 0.1).min(0.96),
        false,
    )
}

fn question_answered(
    prompt: &str,
    answers: &Map<String, Value>,
    question: &ApplicationQuestion,
) -> bool {
    if answers.contains_key(&question.id) {
        return true;
    }
    let lower = prompt.to_lowercase();
    match question.id.as_str() {
        "input_supply" | "output_requirement" | "supply" | "supply_motor" => {
            voltage_is_mentioned(&lower)
        }
        "maximum_load" | "motor_load_current" => {
            lower.contains("amp") || lower.contains("current") || lower.contains("load")
        }
        "sensitivity" => {
            lower.contains("sensitivity")
                || lower.contains("microvolt")
                || lower.contains("precision")
        }
        "range_location" => {
            lower.contains("meter")
                || lower.contains("room")
                || lower.contains("building")
                || lower.contains("outdoor")
        }
        "power_goal" => {
            lower.contains("battery")
                || lower.contains("usb")
                || lower.contains("year")
                || lower.contains("month")
        }
        "interface_speed" => {
            lower.contains("usb") || lower.contains("gigabit") || lower.contains("ethernet")
        }
        "cable_connectors" => {
            lower.contains("cable") || lower.contains("connector") || lower.contains("usb-c")
        }
        "board_constraints" => {
            lower.contains("compact") || lower.contains("small") || lower.contains("board")
        }
        "smoothness_feedback" => {
            lower.contains("feedback") || lower.contains("precise") || lower.contains("smooth")
        }
        "axis_requirements" => {
            lower.contains("axis 1")
                && lower.contains("axis 6")
                && lower.contains("encoder")
                && (lower.contains("amp") || lower.contains("current"))
        }
        "supply_can_topology" => {
            voltage_is_mentioned(&lower)
                && lower.contains("can")
                && (lower.contains("cable")
                    || lower.contains("meter")
                    || lower.contains("termination"))
        }
        "safety_environment" => {
            lower.contains("e-stop")
                && lower.contains("brak")
                && (lower.contains("ambient")
                    || lower.contains("environment")
                    || lower.contains("ip"))
        }
        "usb_c_role" => lower.contains("usb-c") || lower.contains("usb c"),
        "ble_interaction" => lower.contains("ble") || lower.contains("bluetooth"),
        "local_budget" => lower.contains("budget") || lower.contains("units"),
        "signal_type" | "sensing_purpose" | "equipment" | "sensors" | "purpose" | "environment"
        | "priority" => false,
        "industrial_environment" => {
            lower.contains("industrial") || lower.contains("outdoor") || lower.contains("24 v")
        }
        _ => false,
    }
}

fn voltage_is_mentioned(prompt: &str) -> bool {
    prompt.contains("volt")
        || ["3v", "5v", "9v", "12v", "24v", "48v", "120v", "220v"]
            .iter()
            .any(|value| prompt.contains(value))
}

fn build_application_option(
    family: &str,
    index: usize,
    answers: &Map<String, Value>,
) -> ApplicationOption {
    let (name, best_for, benefit, drawback) = match (family, index) {
        ("synchronous_buck_converter", 0) => (
            "Balanced efficiency",
            "Most general embedded products",
            "Good efficiency, manageable heat, and sensible cost",
            "Needs final load and thermal verification",
        ),
        ("synchronous_buck_converter", 1) => (
            "Simple and lower cost",
            "Small loads and prototypes",
            "Fewer parts and easier bring-up",
            "More ripple and less headroom for future load",
        ),
        ("synchronous_buck_converter", _) => (
            "Cool and high-current",
            "Demanding loads or tight temperature limits",
            "More margin for current and heat",
            "Larger, costlier power stage",
        ),
        ("precision_acquisition_board", 0) => (
            "Balanced precision",
            "Reliable product measurements",
            "Strong accuracy without excessive complexity",
            "Needs calibration and noise testing",
        ),
        ("precision_acquisition_board", 1) => (
            "Simple measurement",
            "Trend monitoring and prototypes",
            "Lowest cost and quickest integration",
            "Less sensitivity and environmental margin",
        ),
        ("precision_acquisition_board", _) => (
            "Highest precision",
            "Small signals and demanding environments",
            "Best noise and drift margin",
            "More calibration, shielding, and cost",
        ),
        ("wireless_sensor_2_4ghz", 0) => (
            "Balanced connected sensor",
            "Typical indoor monitoring",
            "Good range, battery life, and development effort",
            "Radio and antenna still need certification testing",
        ),
        ("wireless_sensor_2_4ghz", 1) => (
            "Long-life simple sensor",
            "Low data-rate battery products",
            "Small software and lower power",
            "Less bandwidth and slower updates",
        ),
        ("wireless_sensor_2_4ghz", _) => (
            "Long-range rugged sensor",
            "Difficult locations and long service intervals",
            "More link and environmental margin",
            "Larger antenna, enclosure, and validation burden",
        ),
        ("usb_gigabit_high_speed_board", 0) => (
            "Balanced high-speed link",
            "General data peripherals",
            "Good signal margin with practical cost",
            "Requires controlled routing and connector validation",
        ),
        ("usb_gigabit_high_speed_board", 1) => (
            "Simple short-link board",
            "Short cables and prototypes",
            "Smallest board and easiest build",
            "Shorter reach and lower future speed headroom",
        ),
        ("usb_gigabit_high_speed_board", _) => (
            "Maximum link margin",
            "Long cables or harsh traffic",
            "Best signal integrity and thermal margin",
            "More layers, testing, and cost",
        ),
        ("bldc_servo_controller", 0) => (
            "Balanced smooth servo",
            "Most small robot and actuator joints",
            "Smooth control with practical feedback",
            "Motor, sensor, and safety details remain to prove",
        ),
        ("bldc_servo_controller", 1) => (
            "Simple motor drive",
            "Low-cost prototypes",
            "Fewer sensors and lower cost",
            "Less position accuracy and disturbance rejection",
        ),
        ("bldc_servo_controller", _) => (
            "Precise rugged servo",
            "High duty cycle and precise positioning",
            "Best control, thermal, and safety margin",
            "Largest current stage and validation effort",
        ),
        ("industrial_condition_monitor", 0) => (
            "Balanced industrial monitor",
            "Typical factory equipment",
            "Useful sensing with isolation and serviceability",
            "Needs installation-specific noise and sealing tests",
        ),
        ("industrial_condition_monitor", 1) => (
            "Simple low-cost monitor",
            "Bench trials and basic alarms",
            "Fastest path to a useful prototype",
            "Fewer sensors and less harsh-environment margin",
        ),
        ("industrial_condition_monitor", _) => (
            "Rugged long-life monitor",
            "Outdoor or high-consequence equipment",
            "More protection, sensing, and service interval",
            "Higher enclosure, power, and test cost",
        ),
        ("robotic_joint_capstone", 0) => (
            "Balanced distributed joints",
            "Six-axis robots with independently sized joints",
            "A coordinator and six isolated joint boards keep CAN and E-stop dependencies explicit",
            "Every axis and aggregate bus still need evidence-backed sizing",
        ),
        ("robotic_joint_capstone", 1) => (
            "Prototype distributed joints",
            "Guarded learning platforms",
            "A simpler distributed bring-up path with visible provisional assumptions",
            "Missing braking and safety evidence blocks release",
        ),
        ("robotic_joint_capstone", _) => (
            "Protected distributed joints",
            "Higher duty cycle or safety-assessed robots",
            "More isolation, braking, thermal, and stop-path margin",
            "Largest validation, component, and manufacturing effort",
        ),
        (_, 0) => (
            "Balanced starting concept",
            "A first build with sensible trade-offs",
            "A practical middle ground for size, cost, and performance",
            "Important requirements still need confirmation",
        ),
        (_, 1) => (
            "Simple lower-cost concept",
            "Early prototypes and learning",
            "Fastest and least expensive path to a demonstrator",
            "Less performance and environmental margin",
        ),
        (_, _) => (
            "Higher-margin concept",
            "Demanding performance or safety",
            "More room for performance, ruggedness, or runtime",
            "More size, cost, and verification effort",
        ),
    };
    let mut estimates = Map::new();
    let scale = [1.0, 0.72, 1.35][index];
    estimates.insert("size".into(), json!({"value": format!("{}% of a typical reference board", (scale * 100.0) as u32), "status": "estimate"}));
    estimates.insert(
        "cost".into(),
        json!({"value": format!("about ${:.0} in low volume", 45.0 * scale), "status": "estimate"}),
    );
    estimates.insert(
        "power".into(),
        json!({"value": format!("about {:.1} W typical", 2.5 * scale), "status": "estimate"}),
    );
    estimates.insert("performance".into(), json!({"value": if index == 1 { "basic" } else if index == 2 { "higher margin" } else { "balanced" }, "status": "rule estimate"}));
    let mut requirements = Vec::new();
    for (key, value) in answers {
        requirements.push(format!("{} = {}", key, value));
    }
    if requirements.is_empty() {
        requirements.push("No user sizing values supplied yet".into());
    }
    let mut unresolved = vec![
        "electrical sizing",
        "thermal margin",
        "component evidence",
        "manufacturing profile",
    ];
    if family == "wireless_sensor_2_4ghz" {
        unresolved.push("RF matching and radio compliance");
    }
    if family == "usb_gigabit_high_speed_board" {
        unresolved.push("high-speed signal-integrity simulation");
    }
    if family == "robotic_joint_capstone" || family == "bldc_servo_controller" {
        unresolved.push("motion safety and stop behavior");
    }
    let checks = unresolved
        .iter()
        .map(|gate| ApplicationCheck {
            name: (*gate).into(),
            status: "incomplete".into(),
            message: format!("{} is not verified for this concept yet", gate),
            required_for_release: true,
        })
        .collect();
    let failed = if index == 2 {
        vec!["performance margin not demonstrated".into()]
    } else {
        vec![]
    };
    ApplicationOption {
        id: format!("{}.option_{}", family, index + 1),
        name: name.into(),
        best_for: best_for.into(),
        benefits: vec![benefit.into()],
        drawbacks: vec![drawback.into()],
        estimates,
        assumptions: vec![
            "Estimates use curated family rules and low-volume assembly assumptions".into(),
            "Exact parts, enclosure, and operating limits are not selected yet".into(),
        ],
        what_remains_to_be_proven: unresolved
            .iter()
            .map(|gate| format!("Verify {}", gate))
            .collect(),
        applicable_checks: checks,
        evidence_state: "not_yet_verified".into(),
        unresolved_gates: unresolved.into_iter().map(String::from).collect(),
        failed_gates: failed,
        technical_details: Map::from_iter([
            ("rule_family".into(), json!(family)),
            ("option_index".into(), json!(index + 1)),
            (
                "calculation".into(),
                json!("family baseline × option margin × captured requirements"),
            ),
            ("captured_requirement_keys".into(), json!(requirements)),
        ]),
    }
}

fn robotic_axis_requirements(answers: &Map<String, Value>) -> Result<Vec<Value>, RpcFault> {
    let supplied = answers
        .get("axis_requirements")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    if !supplied.is_empty() && supplied.len() != 6 {
        return Err(RpcFault::invalid(
            "axis_requirements must contain exactly six independently specified rows",
        ));
    }
    let mut rows = Vec::with_capacity(6);
    for index in 0..6 {
        let source = supplied.get(index).and_then(Value::as_object).cloned();
        let mut row = source.clone().unwrap_or_default();
        row.insert("axis_id".into(), json!(format!("axis-{}", index + 1)));
        row.insert("board_role".into(), json!("isolated_joint_controller"));
        if source.is_none() {
            for key in [
                "motor",
                "encoder",
                "brake",
                "current_sense",
                "thermal",
                "safety",
                "cable",
            ] {
                row.insert(key.into(), Value::Null);
            }
            row.insert("evidence_state".into(), json!("incomplete"));
            row.insert("assumptions".into(), json!([
                "No axis values were supplied; this row is provisional and cannot pass sizing or release gates"
            ]));
        } else {
            let required = ["motor", "encoder", "brake", "thermal", "safety"];
            let complete = required
                .iter()
                .all(|key| row.get(*key).is_some_and(|value| !value.is_null()));
            row.insert(
                "evidence_state".into(),
                json!(if complete {
                    "user_specified"
                } else {
                    "incomplete"
                }),
            );
            row.entry("assumptions").or_insert_with(|| json!([]));
        }
        let digest = digest_json(&row)?;
        row.insert("requirements_digest".into(), json!(digest.clone()));
        // A board template can only be reused when this exact requirements
        // digest (and, later, the resulting artifact digest) matches.
        row.insert("template_reuse_key".into(), json!(digest));
        rows.push(Value::Object(row));
    }
    Ok(rows)
}

fn robotic_system_architecture(
    answers: &Map<String, Value>,
    axes: &[Value],
) -> Result<Value, RpcFault> {
    let supply_can = answers
        .get("supply_can_topology")
        .cloned()
        .unwrap_or(Value::Null);
    let safety = answers
        .get("safety_environment")
        .cloned()
        .unwrap_or(Value::Null);
    let input = json!({"axis_requirements": axes, "supply_can_topology": supply_can,
                       "safety_environment": safety});
    let input_digest = digest_json(&input)?;
    let mut architecture = json!({
        "kind": "distributed_six_axis_robot",
        "coordinator": {
            "board_id": "coordinator",
            "role": "central_can_estop_coordinator",
            "paths": ["supply_bus", "can_bus", "estop_chain", "braking_regeneration"]
        },
        "joint_boards": axes.iter().enumerate().map(|(index, axis)| json!({
            "board_id": format!("joint-{}", index + 1),
            "axis_id": format!("axis-{}", index + 1),
            "isolated_configuration": true,
            "requirements_digest": axis["requirements_digest"],
            "template_reuse_key": axis["template_reuse_key"]
        })).collect::<Vec<_>>(),
        "supply_bus": supply_can.clone(),
        "can": {"topology_evidence": answers.get("supply_can_topology").cloned().unwrap_or(Value::Null),
                 "required_termination_count": 2, "node_count": 7},
        "estop": {"propagation": "coordinator_to_all_six_joint_safe_torque_off_inputs",
                   "expectations": safety.clone()},
        "braking": {"path": "joint_dc_link_to_shared_supply_or_local_dissipation",
                     "regeneration_evidence": answers.get("safety_environment").cloned().unwrap_or(Value::Null)},
        "analysis_dependencies": ["aggregate_bus_current", "regulator_loading", "voltage_limits",
            "power_loss", "junction_temperature", "braking_regeneration", "can_loading",
            "estop_propagation", "connector_cable_ratings"],
        "manufacturing_profile": "Eurocircuits",
        "input_digest": input_digest,
    });
    let output_digest = digest_json(&architecture)?;
    architecture["output_digest"] = json!(output_digest);
    Ok(architecture)
}

fn resolve_application(
    prompt: &str,
    answers: &Map<String, Value>,
) -> Result<ApplicationConfiguration, RpcFault> {
    let prompt = prompt.trim();
    if prompt.is_empty() || prompt.chars().count() > 5000 {
        return Err(RpcFault::invalid("prompt must be 1..5000 characters"));
    }
    let (family, family_name, confidence, custom_concept) = classify_application(prompt);
    let questions = application_questions(&family);
    let mut captured = Map::new();
    let mut assumptions = Vec::new();
    for question in &questions {
        if let Some(value) = answers.get(&question.id) {
            captured.insert(
                question.id.clone(),
                json!({"value": value, "source": "user", "verified": true}),
            );
        } else if question_answered(prompt, answers, question) {
            captured.insert(
                question.id.clone(),
                json!({"value": "mentioned in prompt", "source": "prompt", "verified": false}),
            );
            assumptions.push(format!(
                "{} was inferred from the prompt and still needs confirmation",
                question.prompt
            ));
        } else {
            assumptions.push(format!(
                "{} was not provided; a safe family default is used",
                question.prompt
            ));
        }
    }
    let next_questions = questions
        .into_iter()
        .filter(|question| !question_answered(prompt, answers, question))
        .take(3)
        .collect();
    let options = (0..3)
        .map(|index| build_application_option(&family, index, answers))
        .collect();
    let (axis_requirements, system_architecture) = if family == "robotic_joint_capstone" {
        let axes = robotic_axis_requirements(answers)?;
        let architecture = robotic_system_architecture(answers, &axes)?;
        (axes, Some(architecture))
    } else {
        (Vec::new(), None)
    };
    Ok(ApplicationConfiguration {
        schema: "design-studio.application-configuration/1".into(),
        family,
        family_name,
        confidence,
        custom_concept,
        prompt: prompt.into(),
        captured_requirements: captured,
        assumptions,
        next_questions,
        options,
        axis_requirements,
        system_architecture,
    })
}

fn application_resolve(params: ApplicationResolve) -> Result<Value, RpcFault> {
    let application = resolve_application(&params.prompt, &params.answers)?;
    Ok(
        json!({"application": application, "persisted": false, "authoritative_documents_changed": false}),
    )
}

fn application_preview(params: ApplicationPreview) -> Result<Value, RpcFault> {
    let application = resolve_application(&params.prompt, &params.answers)?;
    let option = application
        .options
        .iter()
        .find(|option| option.id == params.option_id)
        .ok_or_else(|| RpcFault::invalid("option_id is not one of the generated options"))?;
    Ok(
        json!({"application": application, "option": option, "persisted": false, "baseline_unchanged": true, "authoritative_documents_changed": false}),
    )
}

fn parse<T: DeserializeOwned>(value: Value) -> Result<T, RpcFault> {
    serde_json::from_value(value).map_err(|e| RpcFault::invalid(format!("invalid params: {e}")))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ProjectOpen {
    manifest_path: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ProjectCreate {
    workspace_root: String,
    name: String,
    #[serde(default)]
    description: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ApplicationResolve {
    prompt: String,
    #[serde(default)]
    answers: Map<String, Value>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ApplicationPreview {
    prompt: String,
    #[serde(default)]
    answers: Map<String, Value>,
    option_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ApplicationApply {
    prompt: String,
    #[serde(default)]
    answers: Map<String, Value>,
    option_id: String,
    #[serde(default)]
    configuration_id: Option<Uuid>,
    approved: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ProductContext {
    root_node_ids: Vec<Uuid>,
    max_nodes: usize,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SelectionUpdate {
    node_id: Uuid,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SlotRead {
    slot_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct VariantGenerate {
    configuration_id: Uuid,
    slot_id: String,
    #[serde(default)]
    objective: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct VariantAction {
    configuration_id: Uuid,
    slot_id: String,
    variant_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ConfigurationSave {
    parent_configuration_id: Uuid,
    equipped: BTreeMap<String, String>,
    #[serde(default)]
    parameter_overrides: Map<String, Value>,
    requirement_profile: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ConfigurationId {
    configuration_id: Uuid,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AnalysisStart {
    configuration_id: Uuid,
    analysis_kind: String,
    #[serde(default)]
    inputs: Value,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PropagationStart {
    configuration_id: Uuid,
    changed_node_ids: Vec<Uuid>,
    #[serde(default)]
    inputs: Map<String, Value>,
    #[serde(default)]
    engine_versions: BTreeMap<String, String>,
    #[serde(default)]
    edit_reason: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct GenerationSubmit {
    configuration_id: Uuid,
    slot_id: String,
    candidate_count: u8,
    seeds: Vec<u64>,
    input_package: Value,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct JobId {
    job_id: Uuid,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BranchCreate {
    name: String,
    domain: String,
    baseline_configuration_id: Uuid,
    #[serde(default)]
    consumes_contracts: Vec<ContractRevision>,
    #[serde(default)]
    proposes_contract_changes: Vec<ContractChange>,
    #[serde(default)]
    artifacts: Vec<String>,
    #[serde(default)]
    evidence_ids: Vec<Uuid>,
    #[serde(default)]
    assumptions: Vec<String>,
    requested_approvals: Vec<String>,
    created_by: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BranchId {
    branch_id: Uuid,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BranchUpdate {
    branch_id: Uuid,
    equipped: BTreeMap<String, String>,
    #[serde(default)]
    parameter_overrides: Map<String, Value>,
    requirement_profile: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct IntegrationCreate {
    baseline_configuration_id: Uuid,
    branch_ids: Vec<Uuid>,
    created_by: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct IntegrationId {
    integration_id: Uuid,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct IntegrationApprove {
    integration_id: Uuid,
    role: String,
    approver: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AgentStart {
    branch_id: Uuid,
    agent_id: String,
    kind: String,
    budget: TaskBudget,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AgentProgress {
    task_id: Uuid,
    consumed_steps: u64,
    consumed_seconds: u64,
    consumed_cost_microunits: u64,
    #[serde(default)]
    complete: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AgentTaskId {
    task_id: Uuid,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ProductCompile {
    intent: ProductIntent,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WorkflowApprove {
    workflow_id: String,
    product_digest: String,
    graph_digest: String,
    approver: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WorkflowAdvance {
    workflow_id: String,
    product_digest: String,
    graph_digest: String,
    approval_digest: String,
    #[serde(default)]
    package_limit: Option<u8>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WorkflowRead {
    workflow_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WorkflowCancel {
    workflow_id: String,
    product_digest: String,
    graph_digest: String,
}

fn validate_product_intent(intent: &ProductIntent) -> Result<(), RpcFault> {
    let valid_id = !intent.product_id.is_empty()
        && intent.product_id.len() <= 96
        && intent.product_id.bytes().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'.' | b'_' | b'-')
        })
        && intent
            .product_id
            .bytes()
            .next()
            .is_some_and(|byte| byte.is_ascii_lowercase());
    if intent.schema != "design-studio.product-intent/1"
        || !valid_id
        || intent.application_family != "desktop_ai_control_console"
        || intent.name.trim().is_empty()
        || intent.name.chars().count() > 200
        || intent.source_product.product != "acceptance/agent-workflow-controller"
        || intent.source_product.project_path
            != "acceptance/agent-workflow-controller/checkpoints/pre-v72-authoritative/agent-workflow-controller.dsproj"
        || intent.source_product.acceptance_report_path
            != "acceptance/agent-workflow-controller/checkpoints/pre-v72-authoritative/acceptance-report.json"
        || intent.requirements.usb_c_connector_mpn != "USB4085-GF-A"
        || intent.requirements.ble_mcu_module_mpn != "ESP32-S3-WROOM-1-N8R8"
        || intent.requirements.host_transport != "usb_c_usb_2"
        || intent.requirements.wireless_transport != "ble_5"
        || !intent.requirements.local_backend
        || intent.requirements.gpu_allowed
        || !intent.requirements.architecture_approval_required
        || intent.budget_units == 0
        || intent.budget_units > 1_000_000
    {
        return Err(RpcFault::invalid(
            "product intent must be the approved local USB-C plus BLE desktop console contract",
        ));
    }
    Ok(())
}

fn validate_source_binding(binding: &SourceProductBinding) -> Result<(), RpcFault> {
    if !is_sha256(&binding.project_digest) || !is_sha256(&binding.acceptance_report_digest) {
        return Err(RpcFault::invalid(
            "source acceptance digests must be SHA-256",
        ));
    }
    let source_root = fs::canonicalize(&binding.source_root)
        .map_err(|error| RpcFault::io("cannot resolve source product root", error))?;
    if !source_root.is_dir() {
        return Err(RpcFault::invalid("source product root must be a directory"));
    }
    let project_relative = Path::new(&binding.project_path);
    let report_relative = Path::new(&binding.acceptance_report_path);
    if project_relative.is_absolute()
        || report_relative.is_absolute()
        || project_relative
            .components()
            .any(|part| matches!(part, Component::ParentDir))
        || report_relative
            .components()
            .any(|part| matches!(part, Component::ParentDir))
    {
        return Err(RpcFault::invalid(
            "source acceptance paths must be repository-relative",
        ));
    }
    let project = source_root.join(project_relative);
    let report = source_root.join(report_relative);
    let canonical_project = fs::canonicalize(&project)
        .map_err(|error| RpcFault::io("cannot resolve source acceptance project", error))?;
    let canonical_report = fs::canonicalize(&report)
        .map_err(|error| RpcFault::io("cannot resolve source acceptance report", error))?;
    if !canonical_project.starts_with(&source_root) || !canonical_report.starts_with(&source_root) {
        return Err(RpcFault::invalid(
            "source acceptance artifacts must remain beneath source_root",
        ));
    }
    let project_bytes = fs::read(canonical_project)
        .map_err(|error| RpcFault::io("cannot read source acceptance project", error))?;
    let report_bytes = fs::read(canonical_report)
        .map_err(|error| RpcFault::io("cannot read source acceptance report", error))?;
    if hex_sha256(&project_bytes) != binding.project_digest
        || hex_sha256(&report_bytes) != binding.acceptance_report_digest
    {
        return Err(RpcFault::state("stale source acceptance project digest"));
    }
    let project_text = String::from_utf8_lossy(&project_bytes);
    let report_text = String::from_utf8_lossy(&report_bytes);
    for approved_mpn in ["USB4085-GF-A", "ESP32-S3-WROOM-1-N8R8"] {
        if !project_text.contains(approved_mpn) || !report_text.contains(approved_mpn) {
            return Err(RpcFault::state(format!(
                "source acceptance project does not contain approved choice {approved_mpn}"
            )));
        }
    }
    Ok(())
}

fn validate_schema<T: Serialize>(
    label: &str,
    schema_text: &str,
    instance: &T,
) -> Result<(), RpcFault> {
    let value = serde_json::to_value(instance)
        .map_err(|error| RpcFault::invalid(format!("cannot serialize {label}: {error}")))?;
    validate_schema_value(label, schema_text, &value)
}

fn validate_schema_value(label: &str, schema_text: &str, instance: &Value) -> Result<(), RpcFault> {
    let schema: Value = serde_json::from_str(schema_text)
        .map_err(|error| RpcFault::state(format!("invalid embedded {label} schema: {error}")))?;
    let compiled = JSONSchema::options()
        .with_draft(Draft::Draft202012)
        .compile(&schema)
        .map_err(|error| {
            RpcFault::state(format!("cannot compile embedded {label} schema: {error}"))
        })?;
    if let Err(errors) = compiled.validate(instance) {
        let messages = errors
            .take(4)
            .map(|error| error.to_string())
            .collect::<Vec<_>>()
            .join("; ");
        return Err(RpcFault::invalid(format!(
            "{label} failed Draft 2020-12 validation: {messages}"
        )));
    }
    Ok(())
}

fn controller_work_packages() -> Vec<WorkPackage> {
    vec![
        WorkPackage {
            id: "component-evidence-cad".into(),
            name: "Reuse exact component datasheet and CAD evidence".into(),
            engine_id: "design-studio.component-workflow/1".into(),
            depends_on: vec![],
            input_refs: vec![
                "acceptance/agent-workflow-controller/generated/evidence-manifest.json".into(),
                "acceptance/agent-workflow-controller/generated/components/USB4085-GF-A/binding.json".into(),
                "acceptance/agent-workflow-controller/generated/components/ESP32-S3-WROOM-1-N8R8/binding.json".into(),
            ],
            budget_units: 10,
        },
        WorkPackage {
            id: "schematic".into(),
            name: "USB-C and BLE controller schematic".into(),
            engine_id: "design-studio.incremental-analysis/1".into(),
            depends_on: vec!["component-evidence-cad".into()],
            input_refs: vec![
                "acceptance/agent-workflow-controller/generated/erc-report.json".into(),
                "acceptance/agent-workflow-controller/generated/schematic.svg".into(),
            ],
            budget_units: 12,
        },
        WorkPackage {
            id: "pcb".into(),
            name: "Native PCB placement routing and DRC".into(),
            engine_id: "designcore-native".into(),
            depends_on: vec!["schematic".into()],
            input_refs: vec![
                "acceptance/agent-workflow-controller/generated/agent-workflow-controller.dsproj".into(),
                "acceptance/agent-workflow-controller/generated/pcb-layout.svg".into(),
                "acceptance/agent-workflow-controller/generated/verification-report.json".into(),
            ],
            budget_units: 18,
        },
        WorkPackage {
            id: "freecad".into(),
            name: "FreeCAD enclosure and assembly integration".into(),
            engine_id: "freecad".into(),
            depends_on: vec!["component-evidence-cad".into(), "pcb".into()],
            input_refs: vec![
                "acceptance/agent-workflow-controller/generated/agent-workflow-controller-board.preview.json".into(),
                "acceptance/agent-workflow-controller/generated/agent-workflow-controller-board.step".into(),
            ],
            budget_units: 14,
        },
        WorkPackage {
            id: "integration".into(),
            name: "Immutable cross-domain integration sandbox".into(),
            engine_id: "design-studio.integration-sandbox/1".into(),
            depends_on: vec!["schematic".into(), "pcb".into(), "freecad".into()],
            input_refs: vec![
                "acceptance/agent-workflow-controller/generated/acceptance-report.json".into(),
                "acceptance/agent-workflow-controller/generated/agent-workflow-controller-v3.dsproj".into(),
            ],
            budget_units: 8,
        },
        WorkPackage {
            id: "verification".into(),
            name: "Unified electrical mechanical and release verification".into(),
            engine_id: "design-studio.verification/1".into(),
            depends_on: vec!["integration".into()],
            input_refs: vec![
                "acceptance/agent-workflow-controller/generated/verification-report.json".into(),
                "acceptance/agent-workflow-controller/generated/manufacturing-release-status.json".into(),
            ],
            budget_units: 10,
        },
    ]
}

fn validate_package_order(packages: &[WorkPackage]) -> Result<(), RpcFault> {
    let mut seen = BTreeSet::new();
    let expected = [
        (
            "component-evidence-cad",
            "design-studio.component-workflow/1",
        ),
        ("schematic", "design-studio.incremental-analysis/1"),
        ("pcb", "designcore-native"),
        ("freecad", "freecad"),
        ("integration", "design-studio.integration-sandbox/1"),
        ("verification", "design-studio.verification/1"),
    ];
    if packages.len() != expected.len() {
        return Err(RpcFault::state(
            "controller graph must contain exactly six registered packages",
        ));
    }
    for (package, (expected_id, expected_engine)) in packages.iter().zip(expected) {
        if package.id.trim().is_empty()
            || package.name.trim().is_empty()
            || package.engine_id.trim().is_empty()
            || package.id != expected_id
            || package.engine_id != expected_engine
            || package.budget_units == 0
            || package.input_refs.is_empty()
            || package
                .depends_on
                .iter()
                .any(|dependency| !seen.contains(dependency))
            || !seen.insert(package.id.clone())
        {
            return Err(RpcFault::state(
                "compiled work-package graph is not a deterministic dependency ordering",
            ));
        }
    }
    Ok(())
}

fn controller_interaction_map() -> Result<Value, RpcFault> {
    let mut map = json!({
        "schema": "design-studio.interaction-surface-map/1",
        "application_family": "desktop_ai_control_console",
        "backend": "local_non_gpu",
        "surfaces": [
            {"id": "intent", "rpc_methods": ["product/compile"],
             "capabilities": ["compile approved USB-C plus BLE requirements"]},
            {"id": "architecture_approval", "rpc_methods": ["workflow/approve"],
             "capabilities": ["record digest-bound architecture approval"]},
            {"id": "workflow_control", "rpc_methods": ["workflow/start", "workflow/cancel", "workflow/resume"],
             "capabilities": ["validate registered subsystem artifacts", "cancel and resume checkpoints"]},
            {"id": "workflow_observation", "rpc_methods": ["workflow/read"],
             "capabilities": ["read checkpoints digests and budgets"]}
        ],
        "map_digest": ""
    });
    let digest = digest_json(&map)?;
    map["map_digest"] = json!(digest);
    Ok(map)
}

fn execute_one_ready_package(
    workspace_root: &Path,
    source_root: &Path,
    graph: &WorkPackageGraph,
    run: &mut WorkflowRun,
) -> Result<Option<String>, RpcFault> {
    let completed: BTreeSet<String> = run
        .checkpoints
        .iter()
        .filter(|checkpoint| checkpoint.status == "succeeded")
        .map(|checkpoint| checkpoint.package_id.clone())
        .collect();
    let next = graph.packages.iter().find(|package| {
        let checkpoint = run
            .checkpoints
            .iter()
            .find(|checkpoint| checkpoint.package_id == package.id)
            .expect("validated checkpoint exists");
        checkpoint.status != "succeeded"
            && checkpoint.status != "rejected_over_budget"
            && package
                .depends_on
                .iter()
                .all(|dependency| completed.contains(dependency))
    });
    let Some(package) = next else { return Ok(None) };
    let dependency_outputs: Vec<String> = package
        .depends_on
        .iter()
        .map(|dependency| {
            run.checkpoints
                .iter()
                .find(|candidate| candidate.package_id == *dependency)
                .and_then(|candidate| candidate.output_digest.clone())
                .expect("completed dependency has output digest")
        })
        .collect();
    let input_artifacts = package
        .input_refs
        .iter()
        .map(|reference| inspect_engine_input(source_root, reference))
        .collect::<Result<Vec<_>, _>>()?;
    let input_digest = digest_json(&json!({
        "product_digest": run.product_digest,
        "graph_digest": run.graph_digest,
        "package_id": package.id,
        "engine_id": package.engine_id,
        "input_artifacts": input_artifacts,
        "dependency_outputs": dependency_outputs
    }))?;
    let checkpoint = run
        .checkpoints
        .iter_mut()
        .find(|checkpoint| checkpoint.package_id == package.id)
        .expect("validated checkpoint exists");
    if package.budget_units > run.budget.remaining_units {
        checkpoint.status = "rejected_over_budget".into();
        checkpoint.input_digest = Some(input_digest);
        if !run.budget.rejected_packages.contains(&package.id) {
            run.budget.rejected_packages.push(package.id.clone());
        }
        run.status = "budget_exhausted".into();
        return Ok(Some(package.id.clone()));
    }

    checkpoint.status = "running".into();
    checkpoint.input_digest = Some(input_digest.clone());
    let engine_output = dispatch_registered_engine(
        source_root,
        package,
        &input_digest,
        &input_artifacts,
        &dependency_outputs,
    )?;
    let output_digest = digest_json(&engine_output)?;
    let relative_result = format!(
        ".designstudio/workflows/{}/{}/result.json",
        run.workflow_id, package.id
    );
    let directory = ensure_local_directory(
        workspace_root,
        &format!(".designstudio/workflows/{}/{}", run.workflow_id, package.id),
    )?;
    atomic_json(&directory.join("result.json"), &engine_output)?;
    checkpoint.status = "succeeded".into();
    checkpoint.output_digest = Some(output_digest);
    checkpoint.result_ref = Some(relative_result);
    checkpoint.consumed_units = package.budget_units;
    run.budget.consumed_units += package.budget_units;
    run.budget.remaining_units -= package.budget_units;
    if run
        .checkpoints
        .iter()
        .all(|checkpoint| checkpoint.status == "succeeded")
    {
        run.status = "succeeded".into();
    }
    Ok(Some(package.id.clone()))
}

fn inspect_engine_input(source_root: &Path, reference: &str) -> Result<Value, RpcFault> {
    let path = Path::new(reference);
    if path.is_absolute()
        || path
            .components()
            .any(|part| matches!(part, Component::ParentDir))
        || !reference.starts_with("acceptance/agent-workflow-controller/generated/")
    {
        return Err(RpcFault::state(format!(
            "unregistered engine input reference: {reference}"
        )));
    }
    let resolved = fs::canonicalize(source_root.join(path)).map_err(|error| {
        RpcFault::io(&format!("cannot resolve engine input {reference}"), error)
    })?;
    if !resolved.starts_with(source_root) {
        return Err(RpcFault::state(format!(
            "engine input escapes the registered source root: {reference}"
        )));
    }
    let bytes = fs::read(resolved)
        .map_err(|error| RpcFault::io(&format!("cannot read engine input {reference}"), error))?;
    Ok(json!({"ref": reference, "digest": hex_sha256(&bytes), "bytes": bytes.len()}))
}

fn dispatch_registered_engine(
    source_root: &Path,
    package: &WorkPackage,
    input_digest: &str,
    input_artifacts: &[Value],
    dependency_outputs: &[String],
) -> Result<Value, RpcFault> {
    let expected_engine = match package.id.as_str() {
        "component-evidence-cad" => "design-studio.component-workflow/1",
        "schematic" => "design-studio.incremental-analysis/1",
        "pcb" => "designcore-native",
        "freecad" => "freecad",
        "integration" => "design-studio.integration-sandbox/1",
        "verification" => "design-studio.verification/1",
        _ => {
            return Err(RpcFault::state(
                "work package is not in the local engine registry",
            ))
        }
    };
    if package.engine_id != expected_engine {
        return Err(RpcFault::state(format!(
            "package {} cannot dispatch to mismatched engine {}",
            package.id, package.engine_id
        )));
    }
    let checks = match package.id.as_str() {
        "component-evidence-cad" => validate_component_engine_artifacts(source_root)?,
        "schematic" => validate_schematic_engine_artifacts(source_root)?,
        "pcb" => validate_pcb_engine_artifacts(source_root)?,
        "freecad" => validate_freecad_engine_artifacts(source_root)?,
        "integration" => validate_integration_engine_artifacts(source_root)?,
        "verification" => validate_verification_engine_artifacts(source_root)?,
        _ => {
            return Err(RpcFault::state(
                "work package is not in the local engine registry",
            ))
        }
    };
    Ok(json!({
        "schema": "design-studio.engine-result/1",
        "package_id": package.id,
        "engine_id": package.engine_id,
        "adapter": "typed_validated_artifact_replay",
        "status": "succeeded",
        "input_digest": input_digest,
        "input_artifacts": input_artifacts,
        "dependency_outputs": dependency_outputs,
        "checks": checks
    }))
}

fn source_json(source_root: &Path, reference: &str, label: &str) -> Result<Value, RpcFault> {
    let path = source_root.join(reference);
    let resolved = fs::canonicalize(&path)
        .map_err(|error| RpcFault::io(&format!("cannot resolve {label}"), error))?;
    if !resolved.starts_with(source_root) {
        return Err(RpcFault::state(format!(
            "{label} escapes the registered source root"
        )));
    }
    read_json(&resolved, label)
}

fn require_json_string<'a>(
    value: &'a Value,
    pointer: &str,
    expected: &str,
    label: &str,
) -> Result<(), RpcFault> {
    if value.pointer(pointer).and_then(Value::as_str) != Some(expected) {
        return Err(RpcFault::state(format!(
            "{label} does not contain the required {pointer}={expected}"
        )));
    }
    Ok(())
}

fn require_empty_array(value: &Value, pointer: &str, label: &str) -> Result<(), RpcFault> {
    if !value
        .pointer(pointer)
        .and_then(Value::as_array)
        .is_some_and(Vec::is_empty)
    {
        return Err(RpcFault::state(format!(
            "{label} has findings at {pointer}"
        )));
    }
    Ok(())
}

fn validate_component_binding(
    source_root: &Path,
    reference: &str,
    expected_mpn: &str,
) -> Result<Value, RpcFault> {
    let binding = source_json(source_root, reference, "bound component")?;
    require_json_string(
        &binding,
        "/schema",
        "design-studio.bound-component/1",
        reference,
    )?;
    require_json_string(&binding, "/status", "complete", reference)?;
    require_json_string(&binding, "/component/mpn", expected_mpn, reference)?;
    require_json_string(&binding, "/model_3d/claimed_mpn", expected_mpn, reference)?;
    require_json_string(
        &binding,
        "/model_3d/alignment_status",
        "verified",
        reference,
    )?;
    let digest = binding
        .pointer("/model_3d/sha256")
        .and_then(Value::as_str)
        .filter(|value| is_sha256(value))
        .ok_or_else(|| RpcFault::state(format!("{reference} has no verified STEP digest")))?;
    Ok(json!({"artifact": reference, "mpn": expected_mpn, "step_sha256": digest}))
}

fn validate_component_engine_artifacts(source_root: &Path) -> Result<Vec<Value>, RpcFault> {
    let manifest_ref = "acceptance/agent-workflow-controller/generated/evidence-manifest.json";
    let manifest = source_json(source_root, manifest_ref, "component evidence manifest")?;
    require_json_string(
        &manifest,
        "/schema",
        "design-studio.agent-controller-evidence/1",
        manifest_ref,
    )?;
    require_json_string(
        &manifest,
        "/release_gate/asset_bindings",
        "pass",
        manifest_ref,
    )?;
    let components = manifest
        .get("components")
        .and_then(Value::as_array)
        .ok_or_else(|| RpcFault::state("component evidence manifest has no components"))?;
    for mpn in ["USB4085-GF-A", "ESP32-S3-WROOM-1-N8R8"] {
        if !components
            .iter()
            .any(|component| component.get("mpn").and_then(Value::as_str) == Some(mpn))
        {
            return Err(RpcFault::state(format!(
                "component evidence manifest is missing {mpn}"
            )));
        }
    }
    Ok(vec![
        json!({"artifact": manifest_ref, "component_count": components.len(),
               "asset_bindings": "pass"}),
        validate_component_binding(
            source_root,
            "acceptance/agent-workflow-controller/generated/components/USB4085-GF-A/binding.json",
            "USB4085-GF-A",
        )?,
        validate_component_binding(
            source_root,
            "acceptance/agent-workflow-controller/generated/components/ESP32-S3-WROOM-1-N8R8/binding.json",
            "ESP32-S3-WROOM-1-N8R8",
        )?,
    ])
}

fn validate_schematic_engine_artifacts(source_root: &Path) -> Result<Vec<Value>, RpcFault> {
    let erc_ref = "acceptance/agent-workflow-controller/generated/erc-report.json";
    let erc = source_json(source_root, erc_ref, "ERC report")?;
    require_json_string(&erc, "/schema", "design-studio.erc/1", erc_ref)?;
    require_empty_array(&erc, "/errors", erc_ref)?;
    require_empty_array(&erc, "/warnings", erc_ref)?;
    let symbols = erc
        .pointer("/metrics/symbols")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let nets = erc
        .pointer("/metrics/nets")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    if symbols == 0 || nets == 0 {
        return Err(RpcFault::state("ERC report contains no schematic topology"));
    }
    Ok(vec![
        json!({"artifact": erc_ref, "erc_errors": 0, "erc_warnings": 0,
                   "symbols": symbols, "nets": nets}),
    ])
}

fn validate_pcb_engine_artifacts(source_root: &Path) -> Result<Vec<Value>, RpcFault> {
    let project_ref =
        "acceptance/agent-workflow-controller/generated/agent-workflow-controller.dsproj";
    let project = source_json(source_root, project_ref, "PCB project")?;
    let footprints = project
        .get("footprints")
        .and_then(Value::as_array)
        .map_or(0, Vec::len);
    let nets = project
        .get("net_table")
        .and_then(Value::as_array)
        .map_or(0, Vec::len);
    if footprints == 0 || nets == 0 {
        return Err(RpcFault::state(
            "PCB project contains no footprints or nets",
        ));
    }
    let verification_ref =
        "acceptance/agent-workflow-controller/generated/verification-report.json";
    let verification = source_json(source_root, verification_ref, "verification report")?;
    require_json_string(
        &verification,
        "/schema",
        "design-studio.verification/1",
        verification_ref,
    )?;
    for category in ["drc", "connectivity"] {
        require_json_string(
            &verification,
            &format!("/categories/{category}/status"),
            "pass",
            verification_ref,
        )?;
    }
    Ok(vec![
        json!({"artifact": project_ref, "footprints": footprints, "nets": nets}),
        json!({"artifact": verification_ref, "drc": "pass",
                          "connectivity": "pass"}),
    ])
}

fn validate_freecad_engine_artifacts(source_root: &Path) -> Result<Vec<Value>, RpcFault> {
    let preview_ref =
        "acceptance/agent-workflow-controller/generated/agent-workflow-controller-board.preview.json";
    let preview = source_json(source_root, preview_ref, "board STEP preview")?;
    require_json_string(
        &preview,
        "/schema",
        "design-studio.board-step-preview/1",
        preview_ref,
    )?;
    require_json_string(&preview, "/kernel_report/step_schema", "AP242", preview_ref)?;
    if preview
        .pointer("/kernel_report/ok")
        .and_then(Value::as_bool)
        != Some(true)
    {
        return Err(RpcFault::state(
            "Open CASCADE board STEP validation did not pass",
        ));
    }
    let step_ref =
        "acceptance/agent-workflow-controller/generated/agent-workflow-controller-board.step";
    let step = fs::read(source_root.join(step_ref))
        .map_err(|error| RpcFault::io("cannot read AP242 board STEP", error))?;
    let digest = hex_sha256(&step);
    if preview.get("step_sha256").and_then(Value::as_str) != Some(digest.as_str()) {
        return Err(RpcFault::state(
            "AP242 board STEP digest does not match its preview",
        ));
    }
    Ok(vec![json!({"artifact": step_ref, "step_sha256": digest,
                   "solids": preview.pointer("/kernel_report/solids"),
                   "components": preview.pointer("/kernel_report/components")})])
}

fn validate_integration_engine_artifacts(source_root: &Path) -> Result<Vec<Value>, RpcFault> {
    let acceptance_ref = "acceptance/agent-workflow-controller/generated/acceptance-report.json";
    let acceptance = source_json(source_root, acceptance_ref, "integration acceptance report")?;
    require_json_string(
        &acceptance,
        "/schema",
        "design-studio.agent-controller-acceptance/1",
        acceptance_ref,
    )?;
    require_json_string(&acceptance, "/status", "pass", acceptance_ref)?;
    for metric in ["drc_errors", "drc_warnings", "erc_errors"] {
        if acceptance
            .pointer(&format!("/metrics/{metric}"))
            .and_then(Value::as_u64)
            != Some(0)
        {
            return Err(RpcFault::state(format!(
                "integration acceptance has nonzero {metric}"
            )));
        }
    }
    Ok(vec![json!({"artifact": acceptance_ref, "status": "pass"})])
}

fn validate_verification_engine_artifacts(source_root: &Path) -> Result<Vec<Value>, RpcFault> {
    let verification_ref =
        "acceptance/agent-workflow-controller/generated/verification-report.json";
    let verification = source_json(source_root, verification_ref, "unified verification report")?;
    require_json_string(
        &verification,
        "/schema",
        "design-studio.verification/1",
        verification_ref,
    )?;
    let categories = verification
        .get("categories")
        .and_then(Value::as_object)
        .ok_or_else(|| RpcFault::state("verification report has no categories"))?;
    if categories.is_empty()
        || categories.values().any(|category| {
            category.get("required").and_then(Value::as_bool) == Some(true)
                && category.get("status").and_then(Value::as_str) != Some("pass")
        })
    {
        return Err(RpcFault::state(
            "one or more required unified verification categories did not pass",
        ));
    }
    let release_ref =
        "acceptance/agent-workflow-controller/generated/manufacturing-release-status.json";
    let release = source_json(source_root, release_ref, "manufacturing release boundary")?;
    require_json_string(
        &release,
        "/schema",
        "design-studio.manufacturing-release-status/1",
        release_ref,
    )?;
    require_json_string(&release, "/engineering_acceptance", "pass", release_ref)?;
    require_json_string(
        &release,
        "/status",
        "blocked-pending-contracted-profile",
        release_ref,
    )?;
    Ok(vec![
        json!({"artifact": verification_ref, "required_categories": categories.len(),
                   "status": "pass"}),
        json!({"artifact": release_ref, "engineering_acceptance": "pass",
                          "manufacturing_release": "blocked-pending-contracted-profile"}),
    ])
}

fn validate_engine_result_artifacts(root: &Path, run: &WorkflowRun) -> Result<(), RpcFault> {
    for checkpoint in run
        .checkpoints
        .iter()
        .filter(|checkpoint| checkpoint.status == "succeeded")
    {
        let relative = checkpoint
            .result_ref
            .as_deref()
            .ok_or_else(|| RpcFault::state("succeeded checkpoint is missing its engine result"))?;
        let path = safe_join(root, relative)?;
        require_beneath(root, &path, relative)?;
        let result: Value = read_json(&path, "persisted engine result")?;
        let output_digest = digest_json(&result)?;
        if checkpoint.output_digest.as_deref() != Some(output_digest.as_str())
            || result["package_id"] != checkpoint.package_id
            || result["status"] != "succeeded"
        {
            return Err(RpcFault::state(
                "persisted engine result digest or identity is stale",
            ));
        }
    }
    Ok(())
}

fn validate_workflow_state(graph: &WorkPackageGraph, run: &WorkflowRun) -> Result<(), RpcFault> {
    validate_package_order(&graph.packages)?;
    let mut graph_material = graph.clone();
    graph_material.graph_id.clear();
    graph_material.graph_digest.clear();
    let computed_graph_digest = digest_json(&graph_material)?;
    let valid_statuses = [
        "awaiting_approval",
        "approved",
        "running",
        "cancelled",
        "succeeded",
        "budget_exhausted",
    ];
    if graph.schema != "design-studio.work-package-graph/1"
        || graph.runnable
        || graph.approval_gate != "architecture"
        || !is_sha256(&graph.product_digest)
        || !is_sha256(&graph.graph_digest)
        || graph.graph_digest != computed_graph_digest
        || graph.graph_id != format!("wpg-{}", &graph.graph_digest[..16])
        || run.schema != "design-studio.workflow-run/1"
        || run.workflow_id != format!("wfr-{}", &graph.graph_digest[..16])
        || run.product_digest != graph.product_digest
        || run.graph_digest != graph.graph_digest
        || !valid_statuses.contains(&run.status.as_str())
        || run.checkpoints.len() != graph.packages.len()
        || run.budget.allocated_units == 0
        || run.budget.consumed_units + run.budget.remaining_units != run.budget.allocated_units
    {
        return Err(RpcFault::state(
            "persisted workflow failed its schema invariants",
        ));
    }
    let mut consumed = 0u64;
    let valid_checkpoint_statuses = [
        "blocked",
        "pending",
        "running",
        "succeeded",
        "cancelled",
        "rejected_over_budget",
    ];
    for (package, checkpoint) in graph.packages.iter().zip(&run.checkpoints) {
        if checkpoint.package_id != package.id
            || !valid_checkpoint_statuses.contains(&checkpoint.status.as_str())
            || checkpoint.consumed_units > package.budget_units
            || (checkpoint.status == "succeeded"
                && (checkpoint.consumed_units != package.budget_units
                    || checkpoint
                        .input_digest
                        .as_deref()
                        .map_or(true, |value| !is_sha256(value))
                    || checkpoint
                        .output_digest
                        .as_deref()
                        .map_or(true, |value| !is_sha256(value))
                    || checkpoint.result_ref.as_deref().map_or(true, str::is_empty)))
            || (checkpoint.status != "succeeded"
                && (checkpoint.output_digest.is_some()
                    || checkpoint.result_ref.is_some()
                    || checkpoint.consumed_units != 0))
        {
            return Err(RpcFault::state(
                "persisted checkpoint failed its schema invariants",
            ));
        }
        consumed += checkpoint.consumed_units;
    }
    if consumed != run.budget.consumed_units {
        return Err(RpcFault::state(
            "workflow checkpoint budget accounting is inconsistent",
        ));
    }
    if run.status != "awaiting_approval" && run.approval.is_none() {
        return Err(RpcFault::state(
            "runnable workflow is missing architecture approval",
        ));
    }
    let succeeded: BTreeSet<&str> = run
        .checkpoints
        .iter()
        .filter(|checkpoint| checkpoint.status == "succeeded")
        .map(|checkpoint| checkpoint.package_id.as_str())
        .collect();
    for package in &graph.packages {
        if succeeded.contains(package.id.as_str())
            && package
                .depends_on
                .iter()
                .any(|dependency| !succeeded.contains(dependency.as_str()))
        {
            return Err(RpcFault::state(
                "succeeded checkpoint has an incomplete dependency",
            ));
        }
    }
    if (run.status == "succeeded" && succeeded.len() != graph.packages.len())
        || (run.status == "awaiting_approval"
            && (run.approval.is_some()
                || run
                    .checkpoints
                    .iter()
                    .any(|checkpoint| checkpoint.status != "blocked")))
        || run.budget.rejected_packages.iter().any(|package_id| {
            !run.checkpoints.iter().any(|checkpoint| {
                checkpoint.package_id == *package_id && checkpoint.status == "rejected_over_budget"
            })
        })
    {
        return Err(RpcFault::state(
            "workflow lifecycle status is inconsistent with checkpoints",
        ));
    }
    if let Some(approval) = &run.approval {
        let expected = digest_json(&json!({
            "kind": "architecture",
            "approver": approval.approver,
            "product_digest": approval.product_digest,
            "graph_digest": approval.graph_digest
        }))?;
        if approval.kind != "architecture"
            || approval.approver.trim().is_empty()
            || approval.product_digest != run.product_digest
            || approval.graph_digest != run.graph_digest
            || approval.approval_digest != expected
        {
            return Err(RpcFault::state(
                "architecture approval failed its schema invariants",
            ));
        }
    }
    Ok(())
}

fn validate_contract_revisions(revisions: &[ContractRevision]) -> Result<(), RpcFault> {
    let mut ids = BTreeSet::new();
    if revisions.iter().any(|contract| {
        contract.contract_id.trim().is_empty()
            || contract.version == 0
            || !is_sha256(&contract.digest)
            || !ids.insert(contract.contract_id.as_str())
    }) {
        return Err(RpcFault::invalid(
            "consumed contracts require unique IDs, versions and SHA-256 digests",
        ));
    }
    Ok(())
}

fn validate_contract_changes(changes: &[ContractChange]) -> Result<(), RpcFault> {
    let mut ids = BTreeSet::new();
    if changes.iter().any(|contract| {
        contract.contract_id.trim().is_empty()
            || contract.base_version == 0
            || contract.proposed_version != contract.base_version + 1
            || !is_sha256(&contract.digest)
            || !ids.insert(contract.contract_id.as_str())
    }) {
        return Err(RpcFault::invalid(
            "contract changes require unique IDs, sequential versions and SHA-256 digests",
        ));
    }
    Ok(())
}

fn validate_manifest(manifest: &WorkspaceManifest, root: &Path) -> Result<(), RpcFault> {
    if manifest.schema != "design-studio.workspace/1"
        || manifest.revision == 0
        || manifest.product.name.trim().is_empty()
    {
        return Err(RpcFault::invalid(
            "unsupported or incomplete workspace manifest",
        ));
    }
    let mut paths = vec![
        &manifest.documents.mechanical,
        &manifest.documents.electronics,
        &manifest.product_graph,
        &manifest.baseline_configuration,
    ];
    paths.extend(manifest.contracts.iter());
    for relative in paths {
        let path = safe_join(root, relative)?;
        if !path.is_file() {
            return Err(RpcFault::invalid(format!(
                "workspace reference does not exist: {relative}"
            )));
        }
        require_beneath(root, &path, relative)?;
    }
    for relative in &manifest.evidence_directories {
        let path = safe_join(root, relative)?;
        if !path.is_dir() {
            return Err(RpcFault::invalid(format!(
                "evidence directory does not exist: {relative}"
            )));
        }
        require_beneath(root, &path, relative)?;
    }
    Ok(())
}

fn validate_graph(graph: &ProductGraph) -> Result<(), RpcFault> {
    if graph.schema != "design-studio.product-graph/1" || graph.revision == 0 {
        return Err(RpcFault::invalid("unsupported product graph"));
    }
    let nodes: BTreeSet<Uuid> = graph.nodes.iter().map(|node| node.id).collect();
    if nodes.len() != graph.nodes.len() {
        return Err(RpcFault::invalid("product node IDs must be unique"));
    }
    const DOMAINS: &[&str] = &[
        "mechanical",
        "electrical",
        "electronic",
        "firmware",
        "manufacturing",
        "product",
    ];
    const AUTHORITIES: &[&str] = &["freecad", "electronics", "product_graph", "contract"];
    if graph.nodes.iter().any(|node| {
        node.name.trim().is_empty()
            || node.source_ref.trim().is_empty()
            || !DOMAINS.contains(&node.domain.as_str())
            || !AUTHORITIES.contains(&node.authority.as_str())
    }) {
        return Err(RpcFault::invalid(
            "product nodes contain an invalid domain, authority, name, or source reference",
        ));
    }
    const EDGE_KINDS: &[&str] = &[
        "contains",
        "depends_on",
        "implements",
        "constrains",
        "connects",
    ];
    if graph.edges.iter().any(|edge| {
        !nodes.contains(&edge.from)
            || !nodes.contains(&edge.to)
            || !EDGE_KINDS.contains(&edge.kind.as_str())
    }) {
        return Err(RpcFault::invalid("product edge references a missing node"));
    }
    let slots: BTreeSet<&str> = graph.slots.iter().map(|slot| slot.id.as_str()).collect();
    if slots.len() != graph.slots.len()
        || graph.slots.iter().any(|slot| {
            !nodes.contains(&slot.node_id)
                || !slot.id.starts_with("slot.")
                || !matches!(
                    slot.allowed_change_class.as_str(),
                    "C0" | "C1" | "C2" | "C3" | "C4" | "C5"
                )
        })
    {
        return Err(RpcFault::invalid(
            "slot IDs must be unique and reference existing nodes",
        ));
    }
    let variants: BTreeSet<&str> = graph
        .variants
        .iter()
        .map(|variant| variant.id.as_str())
        .collect();
    if variants.len() != graph.variants.len()
        || graph.variants.iter().any(|variant| {
            !slots.contains(variant.slot_id.as_str())
                || variant.id.trim().is_empty()
                || !matches!(
                    variant.change_class.as_str(),
                    "C0" | "C1" | "C2" | "C3" | "C4" | "C5"
                )
        })
    {
        return Err(RpcFault::invalid(
            "variant IDs must be unique and reference existing slots",
        ));
    }
    Ok(())
}

fn validate_configuration(
    config: &Configuration,
    manifest: &WorkspaceManifest,
    graph: &ProductGraph,
) -> Result<(), RpcFault> {
    if config.schema != "design-studio.configuration/1"
        || config.workspace_revision != manifest.revision
        || config.graph_revision != graph.revision
        || config.digest != configuration_digest(config)?
    {
        return Err(RpcFault::invalid(
            "baseline configuration has incompatible revisions or an invalid digest",
        ));
    }
    for (slot, variant) in &config.equipped {
        if !graph
            .variants
            .iter()
            .any(|item| item.slot_id == *slot && item.id == *variant)
        {
            return Err(RpcFault::invalid(format!(
                "baseline has incompatible slot/variant pair {slot}/{variant}"
            )));
        }
    }
    Ok(())
}

fn safe_join(root: &Path, relative: &str) -> Result<PathBuf, RpcFault> {
    let candidate = Path::new(relative);
    if candidate.is_absolute()
        || candidate.components().any(|part| {
            matches!(
                part,
                Component::ParentDir | Component::RootDir | Component::Prefix(_)
            )
        })
    {
        return Err(RpcFault::invalid(format!(
            "workspace paths must be relative and cannot traverse parents: {relative}"
        )));
    }
    Ok(root.join(candidate))
}

fn require_beneath(root: &Path, path: &Path, label: &str) -> Result<PathBuf, RpcFault> {
    let canonical = fs::canonicalize(path).map_err(|error| {
        RpcFault::io(
            &format!("cannot resolve workspace reference {label}"),
            error,
        )
    })?;
    if !canonical.starts_with(root) {
        return Err(RpcFault::invalid(format!(
            "workspace reference escapes through a symbolic link: {label}"
        )));
    }
    Ok(canonical)
}

fn ensure_local_directory(root: &Path, relative: &str) -> Result<PathBuf, RpcFault> {
    let directory = safe_join(root, relative)?;
    if directory.exists() {
        let metadata = fs::symlink_metadata(&directory)
            .map_err(|error| RpcFault::io("cannot inspect workspace directory", error))?;
        if metadata.file_type().is_symlink() {
            return Err(RpcFault::state(format!(
                "writable workspace directory cannot be a symbolic link: {relative}"
            )));
        }
    } else {
        fs::create_dir_all(&directory)
            .map_err(|error| RpcFault::io("cannot create workspace directory", error))?;
    }
    require_beneath(root, &directory, relative)
}

fn read_json<T: DeserializeOwned>(path: &Path, context: &str) -> Result<T, RpcFault> {
    let bytes = fs::read(path).map_err(|e| RpcFault::io(&format!("cannot read {context}"), e))?;
    serde_json::from_slice(&bytes).map_err(|e| RpcFault::invalid(format!("invalid {context}: {e}")))
}

fn load_evidence(root: &Path, directories: &[String]) -> Result<Vec<EvidenceRecord>, RpcFault> {
    let mut records = Vec::new();
    for relative in directories {
        let directory = safe_join(root, relative)?;
        for entry in fs::read_dir(&directory)
            .map_err(|e| RpcFault::io("cannot read evidence directory", e))?
        {
            let path = entry
                .map_err(|e| RpcFault::io("cannot read evidence entry", e))?
                .path();
            if path.extension().and_then(|item| item.to_str()) == Some("json") {
                let record: EvidenceRecord = read_json(&path, "evidence record")?;
                if record.schema != "design-studio.evidence/1"
                    || record.gate.trim().is_empty()
                    || record.method.trim().is_empty()
                    || record.source_run.trim().is_empty()
                    || !is_sha256(&record.input_digest)
                    || chrono::DateTime::parse_from_rfc3339(&record.created_utc).is_err()
                {
                    return Err(RpcFault::invalid(format!(
                        "unsupported evidence schema in {}",
                        path.display()
                    )));
                }
                records.push(record);
            }
        }
    }
    Ok(records)
}

fn configuration_digest(config: &Configuration) -> Result<String, RpcFault> {
    let mut material = config.clone();
    material.digest.clear();
    digest_json(&material)
}

fn digest_json(value: &impl Serialize) -> Result<String, RpcFault> {
    let bytes = serde_json::to_vec(value)
        .map_err(|e| RpcFault::invalid(format!("cannot canonicalize input: {e}")))?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}

fn evidence_status_name(status: &EvidenceStatus) -> &'static str {
    match status {
        EvidenceStatus::Pass => "pass",
        EvidenceStatus::Fail => "fail",
        EvidenceStatus::Incomplete => "incomplete",
        EvidenceStatus::NotApplicable => "not_applicable",
    }
}

fn parse_propagation_status(value: &str) -> Option<EvidenceStatus> {
    match value {
        "pass" => Some(EvidenceStatus::Pass),
        "fail" => Some(EvidenceStatus::Fail),
        "incomplete" => Some(EvidenceStatus::Incomplete),
        "not_applicable" => Some(EvidenceStatus::NotApplicable),
        _ => None,
    }
}

/// Decode a solver adapter result without inventing a pass when no exact
/// solver is available.  Adapters may provide an explicit status or a
/// measured value/requirement pair with declared uncertainty.
fn evaluate_propagation_gate(
    supplied: Option<&Value>,
    applicable: bool,
) -> Result<
    (
        EvidenceStatus,
        Option<Value>,
        Option<String>,
        Option<String>,
        Option<f64>,
        Option<Value>,
        Vec<String>,
    ),
    RpcFault,
> {
    if !applicable {
        return Ok((
            EvidenceStatus::NotApplicable,
            None,
            None,
            None,
            None,
            None,
            vec!["gate not reached by affected subgraph".into()],
        ));
    }
    let Some(value) = supplied else {
        return Ok((
            EvidenceStatus::Incomplete,
            None,
            None,
            None,
            None,
            Some(json!({"reason": "exact solver adapter unavailable"})),
            vec!["no result was fabricated".into()],
        ));
    };
    let object = value
        .as_object()
        .ok_or_else(|| RpcFault::invalid("propagation gate input must be an object"))?;
    if let Some(raw_status) = object.get("status") {
        let status = raw_status
            .as_str()
            .and_then(parse_propagation_status)
            .ok_or_else(|| RpcFault::invalid("propagation gate status is invalid"))?;
        let assumptions = object
            .get("assumptions")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .map(|item| {
                        item.as_str().map(str::to_string).ok_or_else(|| {
                            RpcFault::invalid("propagation assumptions must be strings")
                        })
                    })
                    .collect::<Result<Vec<_>, _>>()
            })
            .transpose()?
            .unwrap_or_else(|| vec!["status supplied by an exact solver adapter".into()]);
        return Ok((
            status,
            object.get("value").cloned(),
            object
                .get("unit")
                .and_then(Value::as_str)
                .map(str::to_string),
            object.get("requirement").map(|item| item.to_string()),
            object.get("margin").and_then(Value::as_f64),
            object.get("uncertainty").cloned(),
            assumptions,
        ));
    }
    let value_number = object.get("value").and_then(Value::as_f64);
    let requirement_number = object.get("requirement").and_then(Value::as_f64);
    if let (Some(measured), Some(requirement)) = (value_number, requirement_number) {
        if !measured.is_finite() || !requirement.is_finite() {
            return Err(RpcFault::invalid(
                "propagation gate value and requirement must be finite",
            ));
        }
        let higher_is_better = object
            .get("higher_is_better")
            .and_then(Value::as_bool)
            .unwrap_or(true);
        let raw_margin = if higher_is_better {
            measured - requirement
        } else {
            requirement - measured
        };
        let uncertainty = object
            .get("uncertainty")
            .and_then(Value::as_f64)
            .unwrap_or(0.0);
        if !uncertainty.is_finite() || uncertainty < 0.0 {
            return Err(RpcFault::invalid(
                "propagation uncertainty must be finite and non-negative",
            ));
        }
        let status = if raw_margin - uncertainty >= 0.0 {
            EvidenceStatus::Pass
        } else {
            EvidenceStatus::Fail
        };
        return Ok((
            status,
            Some(json!(measured)),
            object
                .get("unit")
                .and_then(Value::as_str)
                .map(str::to_string),
            Some(requirement.to_string()),
            Some(raw_margin),
            Some(json!(uncertainty)),
            vec!["deterministic gate margin with declared uncertainty".into()],
        ));
    }
    Ok((
        EvidenceStatus::Incomplete,
        None,
        object
            .get("unit")
            .and_then(Value::as_str)
            .map(str::to_string),
        None,
        None,
        Some(json!({"reason": "exact solver adapter unavailable"})),
        vec!["no result was fabricated".into()],
    ))
}

fn propagation_score_changes(input: Option<&Value>) -> Result<Map<String, Value>, RpcFault> {
    let Some(input) = input else {
        return Ok(Map::new());
    };
    let object = input
        .as_object()
        .ok_or_else(|| RpcFault::invalid("propagation scores must be an object"))?;
    let mut output = Map::new();
    for (name, value) in object {
        let record = value
            .as_object()
            .ok_or_else(|| RpcFault::invalid("each propagation score must be an object"))?;
        let before = record.get("before").and_then(Value::as_f64);
        let after = record.get("after").and_then(Value::as_f64);
        let unit = record.get("unit").and_then(Value::as_str).unwrap_or("");
        let (Some(before), Some(after)) = (before, after) else {
            return Err(RpcFault::invalid(
                "each propagation score needs before and after",
            ));
        };
        if !before.is_finite() || !after.is_finite() || unit.trim().is_empty() {
            return Err(RpcFault::invalid(
                "propagation score values must be finite and have a unit",
            ));
        }
        output.insert(
            name.clone(),
            json!({"before": before, "after": after, "delta": after - before, "unit": unit}),
        );
    }
    Ok(output)
}

fn hex_sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn atomic_json(path: &Path, value: &impl Serialize) -> Result<(), RpcFault> {
    let temp = path.with_extension(format!("{}.tmp", Uuid::new_v4()));
    {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)
            .map_err(|e| RpcFault::io("cannot create temporary state", e))?;
        serde_json::to_writer_pretty(&mut file, value)
            .map_err(|e| RpcFault::io("cannot serialize state", e))?;
        file.write_all(b"\n")
            .map_err(|e| RpcFault::io("cannot finish state", e))?;
        file.sync_all()
            .map_err(|e| RpcFault::io("cannot sync state", e))?;
    }
    fs::rename(&temp, path).map_err(|e| RpcFault::io("cannot atomically replace state", e))
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn now() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true)
}

fn gate(code: &str, message: &str) -> Value {
    json!({"code": code, "message": message})
}

fn release_result(status: &str, errors: Vec<Value>, warnings: Vec<Value>) -> Value {
    json!({
        "schema": "design-studio.release-evaluation/1",
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "evaluated_utc": now()
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    #[test]
    fn rejects_parent_traversal() {
        assert!(safe_join(Path::new("/tmp/workspace"), "../secret").is_err());
        assert!(safe_join(Path::new("/tmp/workspace"), "mechanical/product.FCStd").is_ok());
    }

    #[test]
    fn permission_is_fail_closed() {
        let mut service = ControlPlane::default();
        let response: Value = serde_json::from_str(&service.handle_json(
            r#"{"jsonrpc":"2.0","id":1,"method":"product/read","params":{},"auth":{"permissions":[]}}"#,
        )).unwrap();
        assert_eq!(response["error"]["code"], -32003);
    }

    #[test]
    fn unknown_fields_are_rejected() {
        let mut service = ControlPlane::default();
        let response: Value = serde_json::from_str(&service.handle_json(
            r#"{"jsonrpc":"2.0","id":1,"method":"product/read","params":{},"auth":{"permissions":["product:read"]},"surprise":true}"#,
        )).unwrap();
        assert_eq!(response["error"]["code"], -32700);
    }

    #[test]
    fn project_create_builds_a_new_immutable_workspace_baseline() {
        let directory = TempDir::new().unwrap();
        let root = directory.path().join("condition-monitor.dsworkspace");
        let mut service = ControlPlane::default();
        let created = service
            .handle(request(
                1,
                "project/create",
                "project:create",
                json!({
                    "workspace_root": root,
                    "name": "Industrial condition monitor",
                    "description": "24 VDC, isolated RS-485, vibration and temperature"
                }),
            ))
            .unwrap();
        assert!(root.join("manifest.json").is_file());
        assert!(root.join("product-graph.json").is_file());
        assert!(root.join("configurations/baseline.json").is_file());
        assert!(root.join("mechanical").is_dir());
        assert!(root.join("electronics").is_dir());
        assert_eq!(
            created["mechanical_path"],
            json!(root.join("mechanical/product.FCStd"))
        );
        let baseline: Configuration =
            serde_json::from_slice(&fs::read(root.join("configurations/baseline.json")).unwrap())
                .unwrap();
        assert_eq!(baseline.state, ConfigurationState::Committed);
        assert_eq!(baseline.revision, 1);
        assert!(is_sha256(&baseline.digest));
        assert_eq!(baseline.equipped.len(), 3);

        // A committed baseline is selectable in the configurator. Equipping a
        // variant must branch to a sandbox instead of asking the user to mutate
        // or commit the baseline itself.
        fs::write(
            root.join("mechanical/product.FCStd"),
            b"desktop-owned fixture",
        )
        .unwrap();
        fs::write(root.join("electronics/product.dsproj"), b"{}").unwrap();
        service
            .handle(request(
                2,
                "project/open",
                "project:open",
                json!({"manifest_path": root.join("manifest.json")}),
            ))
            .unwrap();
        let equipped = service
            .handle(request(
                3,
                "variant/equip",
                "configuration:write",
                json!({
                    "configuration_id": baseline.configuration_id,
                    "slot_id": "slot.enclosure",
                    "variant_id": "enclosure.unconfigured"
                }),
            ))
            .unwrap();
        assert_eq!(equipped["configuration"]["state"], "sandbox");
        assert_eq!(
            service
                .configuration(baseline.configuration_id)
                .unwrap()
                .state,
            ConfigurationState::Committed
        );

        let second = service.handle(request(
            4,
            "project/create",
            "project:create",
            json!({"workspace_root": root, "name": "Duplicate", "description": ""}),
        ));
        assert!(second.is_err());
    }

    fn fixture() -> (TempDir, Uuid, Uuid, Uuid) {
        let directory = TempDir::new().unwrap();
        let root = directory.path();
        fs::create_dir_all(root.join("mechanical")).unwrap();
        fs::create_dir_all(root.join("electronics")).unwrap();
        fs::create_dir_all(root.join("configurations")).unwrap();
        fs::create_dir_all(root.join("evidence")).unwrap();
        fs::write(
            root.join("mechanical/product.FCStd"),
            b"desktop-owned fixture",
        )
        .unwrap();
        fs::write(root.join("electronics/product.dsproj"), b"{}").unwrap();

        let node_id = Uuid::new_v4();
        let shell_node_id = Uuid::new_v4();
        let workspace_id = Uuid::new_v4();
        let configuration_id = Uuid::new_v4();
        let graph = ProductGraph {
            schema: "design-studio.product-graph/1".into(),
            product_id: Uuid::new_v4(),
            revision: 1,
            nodes: vec![
                ProductNode {
                    id: node_id,
                    name: "Battery".into(),
                    assembly_path: "product/power/battery".into(),
                    domain: "electrical".into(),
                    authority: "product_graph".into(),
                    source_ref: "BAT1".into(),
                    requirements: vec!["PWR-01".into()],
                },
                ProductNode {
                    id: shell_node_id,
                    name: "Upper shell".into(),
                    assembly_path: "product/mechanical/shell/upper".into(),
                    domain: "mechanical".into(),
                    authority: "freecad".into(),
                    source_ref: "UpperShell".into(),
                    requirements: vec!["MECH-01".into(), "CTRL-01".into()],
                },
            ],
            edges: vec![ProductEdge {
                from: shell_node_id,
                to: node_id,
                kind: "constrains".into(),
            }],
            slots: vec![
                Slot {
                    id: "slot.battery".into(),
                    name: "Battery".into(),
                    node_id,
                    assembly_path: "product/power/battery".into(),
                    allowed_change_class: "C3".into(),
                    protected_properties: vec!["connector".into()],
                    customizable_properties: vec!["capacity".into(), "mass".into()],
                    required_evidence: vec!["runtime".into(), "clearance".into(), "control".into()],
                },
                Slot {
                    id: "slot.upper_shell".into(),
                    name: "Upper shell".into(),
                    node_id: shell_node_id,
                    assembly_path: "product/mechanical/shell/upper".into(),
                    allowed_change_class: "C2".into(),
                    protected_properties: vec!["equatorial_interface".into(), "outer_radius".into()],
                    customizable_properties: vec!["wall_thickness".into(), "material".into(), "finish".into()],
                    required_evidence: vec!["mass".into(), "clearance".into(), "control".into()],
                },
            ],
            variants: vec![
                Variant {
                    id: "battery.baseline".into(),
                    slot_id: "slot.battery".into(),
                    name: "Baseline battery".into(),
                    change_class: "C3".into(),
                    parameters: serde_json::from_value(json!({
                        "capacity_ah": {"value": 2.5, "unit": "Ah", "method": "datasheet", "evidence": "verified"},
                        "mass_kg": {"value": 0.22, "unit": "kg", "method": "weighed", "evidence": "verified"},
                        "runtime_h": {"value": 1.4, "unit": "h", "requirement": 1.0, "margin": 0.4, "method": "duty_cycle_energy_model", "evidence": "estimated"}
                    })).unwrap(),
                    failed_gates: vec![],
                    incomplete_gates: vec![],
                },
                Variant {
                    id: "battery.extended".into(),
                    slot_id: "slot.battery".into(),
                    name: "Extended battery".into(),
                    change_class: "C3".into(),
                    parameters: serde_json::from_value(json!({
                        "capacity_ah": {"value": 4.0, "unit": "Ah", "method": "datasheet", "evidence": "verified"},
                        "mass_kg": {"value": 0.34, "unit": "kg", "method": "supplier_model", "evidence": "estimated"},
                        "runtime_h": {"value": 2.3, "unit": "h", "requirement": 1.0, "margin": 1.3, "method": "duty_cycle_energy_model", "evidence": "estimated"},
                        "radial_clearance_mm": {"value": 3.2, "unit": "mm", "requirement": 2.0, "margin": 1.2, "method": "envelope_intersection", "evidence": "calculated"}
                    })).unwrap(),
                    failed_gates: vec![],
                    incomplete_gates: vec![],
                },
                Variant {
                    id: "shell.baseline".into(),
                    slot_id: "slot.upper_shell".into(),
                    name: "Baseline ABS upper shell".into(),
                    change_class: "C2".into(),
                    parameters: serde_json::from_value(json!({
                        "wall_thickness_mm": {"value": 3.0, "unit": "mm", "method": "parametric_cad", "evidence": "calculated"},
                        "mass_kg": {"value": 0.19, "unit": "kg", "method": "brep_volume_density", "evidence": "calculated"},
                        "static_balance_deg": {"value": 4.4, "unit": "deg", "requirement": 3.0, "margin": 1.4, "method": "control_model", "evidence": "calculated"}
                    })).unwrap(),
                    failed_gates: vec![],
                    incomplete_gates: vec![],
                },
                Variant {
                    id: "shell.lightweight".into(),
                    slot_id: "slot.upper_shell".into(),
                    name: "Ribbed PC lightweight shell".into(),
                    change_class: "C2".into(),
                    parameters: serde_json::from_value(json!({
                        "wall_thickness_mm": {"value": 2.2, "unit": "mm", "method": "parametric_cad", "evidence": "calculated"},
                        "mass_kg": {"value": 0.15, "unit": "kg", "requirement": 0.19, "margin": 0.04, "method": "brep_volume_density", "evidence": "calculated"},
                        "static_balance_deg": {"value": 4.7, "unit": "deg", "requirement": 3.0, "margin": 1.7, "method": "control_model", "evidence": "calculated"},
                        "minimum_clearance_mm": {"value": 2.8, "unit": "mm", "requirement": 2.0, "margin": 0.8, "method": "brep_distance", "evidence": "calculated"}
                    })).unwrap(),
                    failed_gates: vec![],
                    incomplete_gates: vec![],
                },
            ],
        };
        fs::write(
            root.join("product-graph.json"),
            serde_json::to_vec_pretty(&graph).unwrap(),
        )
        .unwrap();

        let mut equipped = BTreeMap::new();
        equipped.insert("slot.battery".into(), "battery.baseline".into());
        equipped.insert("slot.upper_shell".into(), "shell.baseline".into());
        let mut baseline = Configuration {
            schema: "design-studio.configuration/1".into(),
            configuration_id,
            revision: 1,
            parent_configuration_id: None,
            baseline_configuration_id: configuration_id,
            workspace_revision: 1,
            graph_revision: 1,
            state: ConfigurationState::Sandbox,
            equipped,
            parameter_overrides: Map::new(),
            requirement_profile: "default".into(),
            created_utc: "2026-07-14T00:00:00.000Z".into(),
            digest: String::new(),
        };
        baseline.digest = configuration_digest(&baseline).unwrap();
        fs::write(
            root.join("configurations/baseline.json"),
            serde_json::to_vec_pretty(&baseline).unwrap(),
        )
        .unwrap();

        let manifest = WorkspaceManifest {
            schema: "design-studio.workspace/1".into(),
            workspace_id,
            revision: 1,
            product: ProductIdentity {
                name: "Fixture".into(),
                description: String::new(),
            },
            documents: Documents {
                mechanical: "mechanical/product.FCStd".into(),
                electronics: "electronics/product.dsproj".into(),
            },
            product_graph: "product-graph.json".into(),
            baseline_configuration: "configurations/baseline.json".into(),
            contracts: vec![],
            evidence_directories: vec!["evidence".into()],
        };
        fs::write(
            root.join("manifest.json"),
            serde_json::to_vec_pretty(&manifest).unwrap(),
        )
        .unwrap();
        (directory, workspace_id, configuration_id, node_id)
    }

    fn request(id: i64, method: &str, permission: &str, params: Value) -> RpcRequest {
        RpcRequest {
            jsonrpc: "2.0".into(),
            id: json!(id),
            method: method.into(),
            params,
            auth: RpcAuth {
                permissions: BTreeSet::from([permission.into()]),
            },
        }
    }

    #[test]
    fn application_catalog_and_sparse_discovery_are_bounded() {
        let mut service = ControlPlane::default();
        let catalog = service
            .handle(request(
                1,
                "application/catalog",
                "application:read",
                json!({}),
            ))
            .unwrap();
        assert_eq!(catalog["families"].as_array().unwrap().len(), 8);
        assert_eq!(catalog["default_option_count"], 3);

        for family in application_family_ids() {
            let result = service
                .handle(request(
                    2,
                    "application/resolve",
                    "application:read",
                    json!({"prompt": application_entry(family).starter_label}),
                ))
                .unwrap();
            let application = &result["application"];
            assert_eq!(application["family"], family);
            assert_eq!(application["options"].as_array().unwrap().len(), 3);
            assert!(application["next_questions"].as_array().unwrap().len() <= 3);
        }
    }

    #[test]
    fn six_axis_robot_resolves_to_six_independent_joint_contracts() {
        let mut service = ControlPlane::default();
        let sparse = service
            .handle(request(
                1,
                "application/resolve",
                "application:read",
                json!({"prompt": "Build a six-axis robotic arm with CAN and E-stop"}),
            ))
            .unwrap();
        let application = &sparse["application"];
        assert_eq!(application["family"], "robotic_joint_capstone");
        assert_eq!(application["next_questions"].as_array().unwrap().len(), 3);
        assert_eq!(application["options"].as_array().unwrap().len(), 3);
        assert_eq!(
            application["axis_requirements"].as_array().unwrap().len(),
            6
        );
        assert_eq!(
            application["system_architecture"]["joint_boards"]
                .as_array()
                .unwrap()
                .len(),
            6
        );
        assert!(application["axis_requirements"]
            .as_array()
            .unwrap()
            .iter()
            .all(|axis| axis["evidence_state"] == "incomplete"));

        let rows: Vec<Value> = (1..=6)
            .map(|axis| {
                json!({
                    "motor": {"type": "BLDC", "bus_voltage_v": 48,
                              "continuous_current_a": axis, "peak_current_a": axis * 2},
                    "encoder": {"type": format!("absolute-{}", axis)},
                    "brake": {"type": "holding", "current_a": 0.2},
                    "thermal": {"ambient_c": 45, "maximum_junction_c": 125},
                    "safety": {"stop_category": 1},
                    "cable": {"length_m": axis}
                })
            })
            .collect();
        let medium = service
            .handle(request(
                2,
                "application/resolve",
                "application:read",
                json!({
                    "prompt": "Six-axis robotic arm", "answers": {"axis_requirements": rows.clone()}
                }),
            ))
            .unwrap();
        assert_eq!(
            medium["application"]["next_questions"]
                .as_array()
                .unwrap()
                .len(),
            2
        );
        assert_eq!(
            medium["application"]["options"].as_array().unwrap().len(),
            3
        );
        let full = service
            .handle(request(
                3,
                "application/resolve",
                "application:read",
                json!({
                    "prompt": "Six-axis robotic arm",
                    "answers": {
                        "axis_requirements": rows,
                        "supply_can_topology": {"minimum_v": 42, "maximum_v": 54,
                            "topology": "daisy_chain", "bit_rate": 1000000,
                            "termination_count": 2, "maximum_cable_m": 12},
                        "safety_environment": {"estop_category": 1, "braking": "regenerative",
                            "ambient_c": 45, "environment": "guarded industrial cell"}
                    }
                }),
            ))
            .unwrap();
        assert_eq!(
            full["application"]["next_questions"]
                .as_array()
                .unwrap()
                .len(),
            0
        );
        let axes = full["application"]["axis_requirements"].as_array().unwrap();
        assert_ne!(
            axes[0]["requirements_digest"],
            axes[1]["requirements_digest"]
        );
        assert_eq!(axes[0]["evidence_state"], "user_specified");
    }

    #[test]
    fn applying_six_axis_robot_creates_coordinator_and_six_board_snapshots() {
        let (directory, _, baseline_id, _) = fixture();
        let mut service = ControlPlane::default();
        service
            .handle(request(
                1,
                "project/open",
                "project:open",
                json!({
                    "manifest_path": directory.path().join("manifest.json")
                }),
            ))
            .unwrap();
        let rows: Vec<Value> = (1..=6)
            .map(|axis| {
                json!({
                    "motor": {"type": "BLDC", "continuous_current_a": axis,
                              "peak_current_a": axis * 2},
                    "encoder": {"type": "absolute"}, "brake": {"type": "holding"},
                    "thermal": {"ambient_c": 40}, "safety": {"stop_category": 1}
                })
            })
            .collect();
        let result = service.handle(request(2, "application/apply", "configuration:write", json!({
            "prompt": "Six-axis robotic arm", "option_id": "robotic_joint_capstone.option_1",
            "configuration_id": baseline_id, "approved": true,
            "answers": {"axis_requirements": rows,
                "supply_can_topology": {"topology": "daisy_chain", "termination_count": 2},
                "safety_environment": {"estop_category": 1, "braking": "local_dump"}}
        }))).unwrap();
        let boards = result["board_configurations"].as_array().unwrap();
        assert_eq!(boards.len(), 7);
        assert_eq!(
            boards
                .iter()
                .filter(|board| board["role"] == "isolated_joint_controller")
                .count(),
            6
        );
        let ids: BTreeSet<&str> = boards
            .iter()
            .map(|board| board["configuration_id"].as_str().unwrap())
            .collect();
        assert_eq!(ids.len(), 7);
        assert_eq!(
            result["distributed_product_graph"]["nodes"]
                .as_array()
                .unwrap()
                .len(),
            7
        );
        assert_eq!(
            result["distributed_product_graph"]["edges"]
                .as_array()
                .unwrap()
                .len(),
            12
        );
        assert_eq!(
            result["configuration"]["parameter_overrides"]["axis_requirements"]
                .as_array()
                .unwrap()
                .len(),
            6
        );
    }

    #[test]
    fn arbitrary_product_uses_disclosed_custom_concept_and_preview_is_non_mutating() {
        let mut service = ControlPlane::default();
        let result = service
            .handle(request(
                1,
                "application/preview",
                "application:read",
                json!({"prompt": "A solar-powered greenhouse pollination device", "option_id": "custom_concept.option_1"}),
            ))
            .unwrap();
        assert_eq!(result["application"]["custom_concept"], true);
        assert_eq!(result["persisted"], false);
        assert_eq!(result["authoritative_documents_changed"], false);
    }

    #[test]
    fn application_apply_requires_approval_and_branches() {
        let (directory, _, baseline_id, _) = fixture();
        let mut service = ControlPlane::default();
        service
            .handle(request(
                1,
                "project/open",
                "project:open",
                json!({
                    "manifest_path": directory.path().join("manifest.json")
                }),
            ))
            .unwrap();
        let denied = service.handle(request(
            2,
            "application/apply",
            "configuration:write",
            json!({
                "prompt": "Build a synchronous buck converter",
                "option_id": "synchronous_buck_converter.option_1",
                "configuration_id": baseline_id,
                "approved": false
            }),
        ));
        assert!(denied.is_err());
        let applied = service
            .handle(request(
                3,
                "application/apply",
                "configuration:write",
                json!({
                    "prompt": "Build a synchronous buck converter",
                    "option_id": "synchronous_buck_converter.option_1",
                    "configuration_id": baseline_id,
                    "approved": true
                }),
            ))
            .unwrap();
        assert_eq!(applied["configuration"]["state"], "sandbox");
        assert_eq!(applied["baseline_unchanged"], true);
        assert_eq!(applied["authoritative_documents_changed"], false);
        assert!(applied["configuration"]["parameter_overrides"]["stage_requirements"].is_object());
    }

    #[test]
    fn equip_and_commit_create_immutable_children() {
        let (directory, _, baseline_id, _) = fixture();
        let mut service = ControlPlane::default();
        service
            .handle(request(
                1,
                "project/open",
                "project:open",
                json!({
                    "manifest_path": directory.path().join("manifest.json")
                }),
            ))
            .unwrap();
        let equipped = service
            .handle(request(
                2,
                "variant/equip",
                "configuration:write",
                json!({
                    "configuration_id": baseline_id,
                    "slot_id": "slot.battery",
                    "variant_id": "battery.extended"
                }),
            ))
            .unwrap();
        let child_id: Uuid =
            serde_json::from_value(equipped["configuration"]["configuration_id"].clone()).unwrap();
        assert_eq!(
            service.configuration(baseline_id).unwrap().equipped["slot.battery"],
            "battery.baseline"
        );
        assert_eq!(
            service.configuration(child_id).unwrap().equipped["slot.battery"],
            "battery.extended"
        );

        let committed = service
            .handle(request(
                3,
                "configuration/commit",
                "configuration:write",
                json!({
                    "configuration_id": child_id
                }),
            ))
            .unwrap();
        let committed_id: Uuid =
            serde_json::from_value(committed["configuration"]["configuration_id"].clone()).unwrap();
        assert_eq!(
            service.configuration(child_id).unwrap().state,
            ConfigurationState::Sandbox
        );
        assert_eq!(
            service.configuration(committed_id).unwrap().state,
            ConfigurationState::Committed
        );
        assert!(directory
            .path()
            .join(".designstudio/events.jsonl")
            .is_file());
    }

    #[test]
    fn release_is_blocked_when_required_evidence_is_missing() {
        let (directory, _, baseline_id, _) = fixture();
        let mut service = ControlPlane::default();
        service
            .handle(request(
                1,
                "project/open",
                "project:open",
                json!({
                    "manifest_path": directory.path().join("manifest.json")
                }),
            ))
            .unwrap();
        let committed = service
            .handle(request(
                2,
                "configuration/commit",
                "configuration:write",
                json!({
                    "configuration_id": baseline_id
                }),
            ))
            .unwrap();
        let committed_id = committed["configuration"]["configuration_id"].clone();
        let evaluation = service
            .handle(request(
                3,
                "release/evaluate",
                "release:evaluate",
                json!({
                    "configuration_id": committed_id
                }),
            ))
            .unwrap();
        assert_eq!(evaluation["status"], "blocked");
        assert_eq!(evaluation["errors"][0]["code"], "EVIDENCE_MISSING");
    }

    #[test]
    fn generation_submission_returns_versioned_bounded_job() {
        let (directory, _, baseline_id, _) = fixture();
        let mut service = ControlPlane::default();
        service
            .handle(request(
                1,
                "project/open",
                "project:open",
                json!({"manifest_path": directory.path().join("manifest.json")}),
            ))
            .unwrap();
        let started = service
            .handle(request(
                2,
                "generation/submit",
                "generation:execute",
                json!({
                    "configuration_id": baseline_id,
                    "slot_id": "slot.battery",
                    "candidate_count": 2,
                    "seeds": [41, 42],
                    "input_package": {"bounding_box_mm": [10, 20, 30]}
                }),
            ))
            .unwrap();
        assert_eq!(started["job"]["schema"], "design-studio.generation-job/1");
        assert_eq!(started["job"]["provider"], "hunyuan3d-omni-amd");
        assert_eq!(started["job"]["candidate_count"], 2);
    }

    #[test]
    fn proposal_branches_integrate_without_mutating_baseline() {
        let (directory, _, baseline_id, node_id) = fixture();
        let mut service = ControlPlane::default();
        service
            .handle(request(
                1,
                "project/open",
                "project:open",
                json!({"manifest_path": directory.path().join("manifest.json")}),
            ))
            .unwrap();
        let committed = service
            .handle(request(
                2,
                "configuration/commit",
                "configuration:write",
                json!({"configuration_id": baseline_id}),
            ))
            .unwrap();
        let committed_id = committed["configuration"]["configuration_id"].clone();
        let digest_a = "a".repeat(64);
        let digest_b = "b".repeat(64);
        let mechanical = service.handle(request(3, "branch/create", "branch:write", json!({
            "name": "Lightweight upper shell", "domain": "mechanical",
            "baseline_configuration_id": committed_id,
            "consumes_contracts": [{"contract_id":"mechanical", "version":1, "digest":digest_a}],
            "proposes_contract_changes": [{"contract_id":"mechanical", "base_version":1,
                "proposed_version":2, "digest":digest_a}],
            "artifacts": ["generated/shell.step"], "evidence_ids": [],
            "assumptions": ["PC material"], "requested_approvals": ["verification"],
            "created_by": "mechanical-agent"
        }))).unwrap();
        let mechanical_id = mechanical["branch"]["branch_id"].clone();
        let baseline_before = service
            .configuration(serde_json::from_value(committed_id.clone()).unwrap())
            .unwrap()
            .digest
            .clone();
        service.handle(request(4, "branch/update", "branch:write", json!({
            "branch_id": mechanical_id,
            "equipped": {"slot.battery":"battery.baseline", "slot.upper_shell":"shell.lightweight"},
            "parameter_overrides": {}, "requirement_profile":"default"
        }))).unwrap();
        assert_eq!(
            service
                .configuration(serde_json::from_value(committed_id.clone()).unwrap())
                .unwrap()
                .digest,
            baseline_before
        );

        let electrical = service
            .handle(request(
                5,
                "branch/create",
                "branch:write",
                json!({
                    "name": "Extended runtime", "domain": "electrical",
                    "baseline_configuration_id": committed_id,
                    "consumes_contracts": [],
                    "proposes_contract_changes": [{"contract_id":"mechanical", "base_version":1,
                        "proposed_version":2, "digest":digest_b}],
                    "artifacts": [], "evidence_ids": [], "assumptions": [],
                    "requested_approvals": ["verification"], "created_by": "electrical-agent"
                }),
            ))
            .unwrap();
        let electrical_id = electrical["branch"]["branch_id"].clone();
        let conflict = service
            .handle(request(
                6,
                "integration/create",
                "integration:write",
                json!({
                    "baseline_configuration_id": committed_id,
                    "branch_ids": [mechanical_id, electrical_id], "created_by":"integrator"
                }),
            ))
            .unwrap();
        let conflict_id = conflict["integration"]["integration_id"].clone();
        let evaluated = service
            .handle(request(
                7,
                "integration/evaluate",
                "integration:write",
                json!({"integration_id": conflict_id}),
            ))
            .unwrap();
        assert_eq!(evaluated["integration"]["status"], "blocked");
        assert!(evaluated["integration"]["checks"]
            .as_array()
            .unwrap()
            .iter()
            .any(|check| check["code"] == "CONTRACT_CONFLICT"));

        let clean = service
            .handle(request(
                8,
                "integration/create",
                "integration:write",
                json!({
                    "baseline_configuration_id": committed_id,
                    "branch_ids": [mechanical_id], "created_by":"integrator"
                }),
            ))
            .unwrap();
        let clean_id = clean["integration"]["integration_id"].clone();
        let ready = service
            .handle(request(
                9,
                "integration/evaluate",
                "integration:write",
                json!({"integration_id": clean_id}),
            ))
            .unwrap();
        assert_eq!(ready["integration"]["status"], "ready_for_approval");
        assert!(service
            .handle(request(
                10,
                "integration/approve",
                "integration:approve",
                json!({
                    "integration_id": clean_id, "role":"verification", "approver":"mechanical-agent"
                })
            ))
            .is_err());
        let approved = service.handle(request(11, "integration/approve", "integration:approve", json!({
            "integration_id": clean_id, "role":"verification", "approver":"verification-agent"
        }))).unwrap();
        assert_eq!(approved["integration"]["status"], "approved");

        let task = service.handle(request(12, "agent/start", "agent:manage", json!({
            "branch_id": mechanical_id, "agent_id":"mechanical-agent", "kind":"cad-proposal",
            "budget":{"max_steps":10,"max_seconds":60,"max_cost_microunits":1000}
        }))).unwrap();
        let task_id = task["task"]["task_id"].clone();
        service
            .handle(request(
                13,
                "agent/progress",
                "agent:manage",
                json!({
                    "task_id":task_id,"consumed_steps":2,"consumed_seconds":5,
                    "consumed_cost_microunits":100,"complete":false
                }),
            ))
            .unwrap();
        assert!(service
            .handle(request(
                14,
                "agent/progress",
                "agent:manage",
                json!({
                    "task_id":task_id,"consumed_steps":11,"consumed_seconds":5,
                    "consumed_cost_microunits":100,"complete":false
                })
            ))
            .is_err());
        let recovered = service
            .handle(request(
                15,
                "agent/recover",
                "agent:manage",
                json!({"task_id":task_id}),
            ))
            .unwrap();
        assert_eq!(recovered["task"]["status"], "queued");
        service
            .handle(request(
                16,
                "agent/cancel",
                "agent:manage",
                json!({"task_id":task_id}),
            ))
            .unwrap();

        let context = service
            .handle(request(
                17,
                "product/context",
                "product:read",
                json!({
                    "root_node_ids":[node_id], "max_nodes":1
                }),
            ))
            .unwrap();
        assert_eq!(context["nodes"].as_array().unwrap().len(), 1);
    }

    fn console_intent(budget_units: u64) -> Value {
        let manifest_dir = std::env::var("CARGO_MANIFEST_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| std::env::current_dir().unwrap());
        let repository = manifest_dir.join("../..");
        let repository = fs::canonicalize(repository).unwrap();
        let project_path = "acceptance/agent-workflow-controller/checkpoints/pre-v72-authoritative/agent-workflow-controller.dsproj";
        let report_path = "acceptance/agent-workflow-controller/checkpoints/pre-v72-authoritative/acceptance-report.json";
        json!({
            "schema": "design-studio.product-intent/1",
            "product_id": "desktop-ai-control-console",
            "application_family": "desktop_ai_control_console",
            "name": "USB-C plus BLE desktop AI control console",
            "source_product": {
                "product": "acceptance/agent-workflow-controller",
                "source_root": repository.to_string_lossy(),
                "project_path": project_path,
                "project_digest": hex_sha256(&fs::read(repository.join(project_path)).unwrap()),
                "acceptance_report_path": report_path,
                "acceptance_report_digest": hex_sha256(&fs::read(repository.join(report_path)).unwrap())
            },
            "requirements": {
                "usb_c_connector_mpn": "USB4085-GF-A",
                "ble_mcu_module_mpn": "ESP32-S3-WROOM-1-N8R8",
                "host_transport": "usb_c_usb_2",
                "wireless_transport": "ble_5",
                "local_backend": true,
                "gpu_allowed": false,
                "architecture_approval_required": true
            },
            "budget_units": budget_units
        })
    }

    fn compiled_console(budget_units: u64) -> (TempDir, ControlPlane, Value) {
        let (directory, _, _, _) = fixture();
        let mut service = ControlPlane::default();
        service
            .handle(request(
                1,
                "project/open",
                "project:open",
                json!({"manifest_path": directory.path().join("manifest.json")}),
            ))
            .unwrap();
        let compiled = service
            .handle(request(
                2,
                "product/compile",
                "workflow:compile",
                json!({"intent": console_intent(budget_units)}),
            ))
            .unwrap();
        (directory, service, compiled)
    }

    #[test]
    fn console_compilation_is_deterministic_ordered_and_reuses_registered_engines() {
        let (_directory, mut service, first) = compiled_console(72);
        let second = service
            .handle(request(
                3,
                "product/compile",
                "workflow:compile",
                json!({"intent": console_intent(72)}),
            ))
            .unwrap();
        assert_eq!(first["product_digest"], second["product_digest"]);
        assert_eq!(first["work_package_graph"], second["work_package_graph"]);
        let packages = first["work_package_graph"]["packages"].as_array().unwrap();
        assert_eq!(
            packages
                .iter()
                .map(|package| package["id"].as_str().unwrap())
                .collect::<Vec<_>>(),
            vec![
                "component-evidence-cad",
                "schematic",
                "pcb",
                "freecad",
                "integration",
                "verification"
            ]
        );
        assert_eq!(
            packages[3]["depends_on"],
            json!(["component-evidence-cad", "pcb"])
        );
        assert_eq!(
            packages
                .iter()
                .map(|package| package["engine_id"].as_str().unwrap())
                .collect::<Vec<_>>(),
            vec![
                "design-studio.component-workflow/1",
                "design-studio.incremental-analysis/1",
                "designcore-native",
                "freecad",
                "design-studio.integration-sandbox/1",
                "design-studio.verification/1"
            ]
        );
        assert_eq!(first["work_package_graph"]["runnable"], false);
        assert_eq!(first["interaction_surface_map"]["backend"], "local_non_gpu");
    }

    #[test]
    fn workflow_requires_fresh_approval_and_cancel_resume_preserves_charges() {
        let (directory, mut service, compiled) = compiled_console(72);
        let workflow_id = compiled["workflow"]["workflow_id"].clone();
        let product_digest = compiled["product_digest"].clone();
        let graph_digest = compiled["work_package_graph"]["graph_digest"].clone();
        let before_approval = service.handle(request(
            3,
            "workflow/start",
            "workflow:execute",
            json!({"workflow_id": workflow_id, "product_digest": product_digest,
                   "graph_digest": graph_digest, "approval_digest": "0".repeat(64)}),
        ));
        assert!(before_approval
            .unwrap_err()
            .message
            .contains("architecture approval"));
        assert!(service
            .handle(request(
                4,
                "workflow/approve",
                "workflow:approve",
                json!({"workflow_id": workflow_id, "product_digest": product_digest,
                       "graph_digest": "0".repeat(64), "approver": "architecture-owner"}),
            ))
            .unwrap_err()
            .message
            .contains("stale"));
        let approved = service
            .handle(request(
                5,
                "workflow/approve",
                "workflow:approve",
                json!({"workflow_id": workflow_id, "product_digest": product_digest,
                       "graph_digest": graph_digest, "approver": "architecture-owner"}),
            ))
            .unwrap();
        let approval_digest = approved["workflow"]["approval"]["approval_digest"].clone();
        assert!(service
            .handle(request(
                6,
                "workflow/start",
                "workflow:execute",
                json!({"workflow_id": workflow_id, "product_digest": product_digest,
                       "graph_digest": graph_digest, "approval_digest": "0".repeat(64)})
            ))
            .unwrap_err()
            .message
            .contains("stale architecture approval"));
        let started = service
            .handle(request(
                7,
                "workflow/start",
                "workflow:execute",
                json!({"workflow_id": workflow_id, "product_digest": product_digest,
                       "graph_digest": graph_digest, "approval_digest": approval_digest,
                       "package_limit": 1}),
            ))
            .unwrap();
        assert_eq!(started["workflow"]["budget"]["consumed_units"], 10);
        assert_eq!(started["workflow"]["checkpoints"][0]["status"], "succeeded");
        let cancelled = service
            .handle(request(
                8,
                "workflow/cancel",
                "workflow:execute",
                json!({"workflow_id": workflow_id, "product_digest": product_digest,
                       "graph_digest": graph_digest}),
            ))
            .unwrap();
        assert_eq!(cancelled["workflow"]["budget"]["consumed_units"], 10);
        assert_eq!(
            cancelled["workflow"]["checkpoints"][0]["status"],
            "succeeded"
        );
        let mut resumed = Value::Null;
        for identifier in 9..14 {
            resumed = service
                .handle(request(
                    identifier,
                    "workflow/resume",
                    "workflow:execute",
                    json!({"workflow_id": workflow_id, "product_digest": product_digest,
                           "graph_digest": graph_digest, "approval_digest": approval_digest}),
                ))
                .unwrap();
        }
        assert_eq!(resumed["workflow"]["status"], "succeeded");
        assert_eq!(resumed["workflow"]["budget"]["consumed_units"], 72);
        assert_eq!(resumed["workflow"]["budget"]["remaining_units"], 0);
        assert!(resumed["workflow"]["checkpoints"]
            .as_array()
            .unwrap()
            .iter()
            .all(
                |checkpoint| checkpoint["input_digest"].as_str().unwrap().len() == 64
                    && checkpoint["output_digest"].as_str().unwrap().len() == 64
            ));
        assert!(service
            .handle(request(
                14,
                "workflow/resume",
                "workflow:execute",
                json!({"workflow_id": workflow_id, "product_digest": product_digest,
                       "graph_digest": graph_digest, "approval_digest": approval_digest})
            ))
            .is_err());
        let read = service
            .handle(request(
                15,
                "workflow/read",
                "workflow:read",
                json!({"workflow_id": workflow_id}),
            ))
            .unwrap();
        assert_eq!(read["workflow"]["budget"]["consumed_units"], 72);
        let mut restarted = ControlPlane::default();
        restarted
            .handle(request(
                16,
                "project/open",
                "project:open",
                json!({"manifest_path": directory.path().join("manifest.json")}),
            ))
            .unwrap();
        let persisted = restarted
            .handle(request(
                17,
                "workflow/read",
                "workflow:read",
                json!({"workflow_id": workflow_id}),
            ))
            .unwrap();
        assert_eq!(persisted["workflow"]["status"], "succeeded");
        assert_eq!(persisted["workflow"]["budget"]["consumed_units"], 72);
    }

    #[test]
    fn over_budget_package_is_rejected_without_double_charge() {
        let (_directory, mut service, compiled) = compiled_console(15);
        let workflow_id = compiled["workflow"]["workflow_id"].clone();
        let product_digest = compiled["product_digest"].clone();
        let graph_digest = compiled["work_package_graph"]["graph_digest"].clone();
        let approved = service
            .handle(request(
                3,
                "workflow/approve",
                "workflow:approve",
                json!({"workflow_id": workflow_id, "product_digest": product_digest,
                       "graph_digest": graph_digest, "approver": "architecture-owner"}),
            ))
            .unwrap();
        let approval_digest = approved["workflow"]["approval"]["approval_digest"].clone();
        let started = service
            .handle(request(
                4,
                "workflow/start",
                "workflow:execute",
                json!({"workflow_id": workflow_id, "product_digest": product_digest,
                       "graph_digest": graph_digest, "approval_digest": approval_digest}),
            ))
            .unwrap();
        assert_eq!(started["workflow"]["status"], "running");
        let result = service
            .handle(request(
                5,
                "workflow/resume",
                "workflow:execute",
                json!({"workflow_id": workflow_id, "product_digest": product_digest,
                       "graph_digest": graph_digest, "approval_digest": approval_digest}),
            ))
            .unwrap();
        assert_eq!(result["workflow"]["status"], "budget_exhausted");
        assert_eq!(result["workflow"]["budget"]["consumed_units"], 10);
        assert_eq!(result["workflow"]["budget"]["remaining_units"], 5);
        assert_eq!(
            result["workflow"]["budget"]["rejected_packages"],
            json!(["schematic"])
        );
        assert_eq!(
            result["workflow"]["checkpoints"][1]["status"],
            "rejected_over_budget"
        );
    }

    #[test]
    fn schema_and_semantic_validation_reject_invalid_runtime_artifacts() {
        let invalid_intent = json!({
            "schema": "design-studio.product-intent/1",
            "product_id": "console",
            "application_family": "desktop_ai_control_console",
            "name": "invalid",
            "source_product": {},
            "requirements": {},
            "budget_units": 1
        });
        assert!(validate_schema_value(
            "product intent",
            include_str!("../../../docs/schemas/product-intent-v1.schema.json"),
            &invalid_intent,
        )
        .is_err());

        let mut packages = controller_work_packages();
        packages[0].depends_on = vec!["verification".into()];
        assert!(validate_package_order(&packages).is_err());
        let mut packages = controller_work_packages();
        packages[2].engine_id = "freecad".into();
        assert!(validate_package_order(&packages).is_err());

        let (_directory, _service, compiled) = compiled_console(72);
        let graph: WorkPackageGraph =
            serde_json::from_value(compiled["work_package_graph"].clone()).unwrap();
        let mut run: WorkflowRun = serde_json::from_value(compiled["workflow"].clone()).unwrap();
        run.budget.remaining_units -= 1;
        assert!(validate_workflow_state(&graph, &run).is_err());
    }

    #[test]
    fn engine_dispatch_persists_real_result_before_checkpoint_return() {
        let (directory, mut service, compiled) = compiled_console(72);
        let workflow_id = compiled["workflow"]["workflow_id"].clone();
        let product_digest = compiled["product_digest"].clone();
        let graph_digest = compiled["work_package_graph"]["graph_digest"].clone();
        let approved = service
            .handle(request(
                3,
                "workflow/approve",
                "workflow:approve",
                json!({"workflow_id": workflow_id, "product_digest": product_digest,
                       "graph_digest": graph_digest, "approver": "architecture-owner"}),
            ))
            .unwrap();
        let approval_digest = approved["workflow"]["approval"]["approval_digest"].clone();
        let started = service
            .handle(request(
                4,
                "workflow/start",
                "workflow:execute",
                json!({"workflow_id": workflow_id, "product_digest": product_digest,
                       "graph_digest": graph_digest, "approval_digest": approval_digest}),
            ))
            .unwrap();
        let checkpoint = &started["workflow"]["checkpoints"][0];
        let result_ref = checkpoint["result_ref"].as_str().unwrap();
        let result_path = directory.path().join(result_ref);
        assert!(result_path.is_file());
        let engine_result: Value = serde_json::from_slice(&fs::read(result_path).unwrap()).unwrap();
        assert_eq!(
            engine_result["engine_id"],
            "design-studio.component-workflow/1"
        );
        assert_eq!(engine_result["adapter"], "typed_validated_artifact_replay");
        assert!(engine_result["checks"]
            .as_array()
            .is_some_and(|checks| !checks.is_empty()));
        assert_eq!(
            digest_json(&engine_result).unwrap(),
            checkpoint["output_digest"]
        );
        let events =
            fs::read_to_string(directory.path().join(".designstudio/events.jsonl")).unwrap();
        assert!(events
            .lines()
            .any(|line| line.contains("\"event\":\"workflow/checkpoint\"")));

        let state: Value = serde_json::from_slice(
            &fs::read(directory.path().join(".designstudio/state.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(
            state["workflows"][workflow_id.as_str().unwrap()]["checkpoints"][0]["status"],
            "succeeded"
        );
    }

    #[test]
    fn stale_source_binding_is_rejected_before_resume() {
        let (_directory, mut service, compiled) = compiled_console(72);
        let workflow_id = compiled["workflow"]["workflow_id"]
            .as_str()
            .unwrap()
            .to_owned();
        let product_digest = compiled["product_digest"].as_str().unwrap().to_owned();
        let graph_digest = compiled["work_package_graph"]["graph_digest"]
            .as_str()
            .unwrap()
            .to_owned();
        let approved = service
            .handle(request(
                3,
                "workflow/approve",
                "workflow:approve",
                json!({"workflow_id": workflow_id, "product_digest": product_digest,
                       "graph_digest": graph_digest, "approver": "architecture-owner"}),
            ))
            .unwrap();
        let approval_digest = approved["workflow"]["approval"]["approval_digest"].clone();
        service
            .state
            .product_intents
            .get_mut(&product_digest)
            .unwrap()
            .source_product
            .project_digest = "0".repeat(64);
        let error = service
            .handle(request(
                4,
                "workflow/start",
                "workflow:execute",
                json!({"workflow_id": workflow_id, "product_digest": product_digest,
                       "graph_digest": graph_digest, "approval_digest": approval_digest}),
            ))
            .unwrap_err();
        assert!(error.message.contains("stale source acceptance"));
    }

    #[test]
    fn engine_adapter_failure_cannot_succeed_or_charge_budget() {
        let empty_source = tempfile::tempdir().unwrap();
        let packages = controller_work_packages();
        let error = dispatch_registered_engine(
            empty_source.path(),
            &packages[0],
            &"a".repeat(64),
            &[],
            &[],
        )
        .unwrap_err();
        assert!(error.message.contains("component evidence manifest"));

        let (directory, mut service, compiled) = compiled_console(72);
        let workflow_id = compiled["workflow"]["workflow_id"].clone();
        let product_digest = compiled["product_digest"].clone();
        let graph_digest = compiled["work_package_graph"]["graph_digest"].clone();
        let approved = service
            .handle(request(
                3,
                "workflow/approve",
                "workflow:approve",
                json!({"workflow_id": workflow_id, "product_digest": product_digest,
                       "graph_digest": graph_digest, "approver": "architecture-owner"}),
            ))
            .unwrap();
        let approval_digest = approved["workflow"]["approval"]["approval_digest"].clone();
        let source_root = directory.path().join("missing-source-root");
        fs::create_dir(&source_root).unwrap();
        let intent = service
            .state
            .product_intents
            .get_mut(product_digest.as_str().unwrap())
            .unwrap();
        intent.source_product.source_root = source_root.to_string_lossy().into_owned();
        let result = service.handle(request(
            4,
            "workflow/start",
            "workflow:execute",
            json!({"workflow_id": workflow_id, "product_digest": product_digest,
                   "graph_digest": graph_digest, "approval_digest": approval_digest}),
        ));
        assert!(result.is_err());
        let persisted = service
            .state
            .workflows
            .get(workflow_id.as_str().unwrap())
            .unwrap();
        assert_eq!(persisted.budget.consumed_units, 0);
        assert_ne!(persisted.checkpoints[0].status, "succeeded");
    }

    #[cfg(unix)]
    #[test]
    fn workspace_reference_cannot_escape_through_symlink() {
        use std::os::unix::fs::symlink;

        let (directory, _, _, _) = fixture();
        let outside = TempDir::new().unwrap();
        let outside_graph = outside.path().join("graph.json");
        fs::write(&outside_graph, b"{}").unwrap();
        fs::remove_file(directory.path().join("product-graph.json")).unwrap();
        symlink(&outside_graph, directory.path().join("product-graph.json")).unwrap();

        let mut service = ControlPlane::default();
        let error = service
            .handle(request(
                1,
                "project/open",
                "project:open",
                json!({"manifest_path": directory.path().join("manifest.json")}),
            ))
            .unwrap_err();
        assert!(error.message.contains("escapes through a symbolic link"));
    }

    #[test]
    fn restart_rejects_corrupt_persisted_configuration() {
        let (directory, _, _, _) = fixture();
        let manifest_path = directory.path().join("manifest.json");
        let mut original = ControlPlane::default();
        original
            .handle(request(
                1,
                "project/open",
                "project:open",
                json!({"manifest_path": manifest_path}),
            ))
            .unwrap();

        let state_path = directory.path().join(".designstudio/state.json");
        let mut state: Value = serde_json::from_slice(&fs::read(&state_path).unwrap()).unwrap();
        let configurations = state["configurations"].as_object_mut().unwrap();
        let configuration = configurations
            .values_mut()
            .next()
            .unwrap()
            .as_object_mut()
            .unwrap();
        configuration.insert("digest".into(), Value::String("0".repeat(64)));
        fs::write(&state_path, serde_json::to_vec_pretty(&state).unwrap()).unwrap();

        let mut restarted = ControlPlane::default();
        let error = restarted
            .handle(request(
                2,
                "project/open",
                "project:open",
                json!({"manifest_path": directory.path().join("manifest.json")}),
            ))
            .unwrap_err();
        assert!(error.message.contains("corrupt configuration"));
    }
}
