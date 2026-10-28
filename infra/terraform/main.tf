# Baseline infrastructure for ShopFloor Saathi on Google Cloud.
# NOTE: written to follow Google's Well-Architected guidance (least privilege, no keys, private by default),
# but NOT applied or validated in this repo's CI. Run `terraform init && terraform validate && terraform plan`
# in a dev project before use.

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "asia-south1"
}
variable "invoker_members" {
  description = "Who may reach the service, e.g. [\"domain:example.com\"]. Empty = nobody (private)."
  type        = list(string)
  default     = []
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  apis = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "firestore.googleapis.com",
    "aiplatform.googleapis.com",
    "secretmanager.googleapis.com",
    "identitytoolkit.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  repository_id = "saathi"
  format        = "DOCKER"
  location      = var.region
  depends_on    = [google_project_service.apis]
}

resource "google_firestore_database" "default" {
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"
  depends_on  = [google_project_service.apis]
}

# Runtime identity: only what the service needs.
resource "google_service_account" "run" {
  account_id   = "saathi-run"
  display_name = "ShopFloor Saathi runtime"
}

resource "google_project_iam_member" "run_roles" {
  for_each = toset([
    "roles/aiplatform.user",           # call Gemini on Vertex AI
    "roles/datastore.user",            # read/write work orders in Firestore
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.run.email}"
}

# Cloud Build identity may push images and deploy as the runtime service account.
data "google_project" "this" {}

resource "google_project_iam_member" "build_roles" {
  for_each = toset(["roles/run.developer", "roles/artifactregistry.writer", "roles/logging.logWriter"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${data.google_project.this.number}@cloudbuild.gserviceaccount.com"
}

resource "google_service_account_iam_member" "build_act_as_run" {
  service_account_id = google_service_account.run.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${data.google_project.this.number}@cloudbuild.gserviceaccount.com"
}

# The first deploy is done by cloudbuild.yaml; this just declares the invoker policy for the service.
resource "google_cloud_run_v2_service_iam_member" "invokers" {
  for_each = toset(var.invoker_members)
  location = var.region
  name     = "shopfloor-saathi"
  role     = "roles/run.invoker"
  member   = each.value
}

# Budget guard rail for LLM cost: alert on spend. (Create a billing budget in the console or with
# google_billing_budget once the billing account id is known.)

output "image_repo" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}
output "runtime_service_account" {
  value = google_service_account.run.email
}
