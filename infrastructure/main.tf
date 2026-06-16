locals {
  name_prefix = "fomoccs"
  labels = {
    project    = "fomoccs"
    managed-by = "terraform"
  }
}

# ─── APIs ────────────────────────────────────────────────────────────────────

resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "vpcaccess.googleapis.com",
    "cloudscheduler.googleapis.com",
    "compute.googleapis.com",
    "servicenetworking.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# ─── Auto-generated secrets ───────────────────────────────────────────────────

resource "random_password" "db_password" {
  length  = 32
  special = false
}

resource "random_password" "secret_key" {
  length  = 64
  special = false
}

# ─── Artifact Registry ───────────────────────────────────────────────────────

resource "google_artifact_registry_repository" "docker" {
  repository_id = "${local.name_prefix}-docker"
  location      = var.region
  format        = "DOCKER"
  labels        = local.labels

  depends_on = [google_project_service.apis]
}

# ─── VPC & Networking ────────────────────────────────────────────────────────

resource "google_compute_network" "vpc" {
  name                    = "${local.name_prefix}-vpc"
  auto_create_subnetworks = false

  depends_on = [google_project_service.apis]
}

resource "google_compute_subnetwork" "subnet" {
  name          = "${local.name_prefix}-subnet"
  ip_cidr_range = "10.0.0.0/24"
  region        = var.region
  network       = google_compute_network.vpc.id
}

resource "google_compute_global_address" "private_ip" {
  name          = "${local.name_prefix}-private-ip"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_vpc" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip.name]
}

resource "google_vpc_access_connector" "connector" {
  name          = "${local.name_prefix}-vpc"
  region        = var.region
  network       = google_compute_network.vpc.name
  ip_cidr_range = "10.8.0.0/28"

  depends_on = [google_project_service.apis]
}

# ─── Cloud SQL (PostgreSQL) ──────────────────────────────────────────────────

resource "google_sql_database_instance" "postgres" {
  name             = "${local.name_prefix}-db"
  database_version = "POSTGRES_15"
  region           = var.region

  settings {
    tier              = "db-f1-micro"
    availability_type = "ZONAL"

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = google_compute_network.vpc.id
      enable_private_path_for_google_cloud_services = true
    }

    user_labels = local.labels
  }

  deletion_protection = false

  depends_on = [google_service_networking_connection.private_vpc]
}

resource "google_sql_database" "fomoccs" {
  name     = "fomoccs"
  instance = google_sql_database_instance.postgres.name
}

resource "google_sql_user" "fomoccs" {
  name     = "fomoccs"
  instance = google_sql_database_instance.postgres.name
  password = random_password.db_password.result
}

# ─── Secret Manager ──────────────────────────────────────────────────────────

resource "google_secret_manager_secret" "db_password" {
  secret_id = "${local.name_prefix}-db-password"
  labels    = local.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db_password.result
}

resource "google_secret_manager_secret" "secret_key" {
  secret_id = "${local.name_prefix}-secret-key"
  labels    = local.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "secret_key" {
  secret      = google_secret_manager_secret.secret_key.id
  secret_data = random_password.secret_key.result
}

resource "google_secret_manager_secret" "gemini_api_key" {
  secret_id = "${local.name_prefix}-gemini-api-key"
  labels    = local.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

# Gemini API key value is added manually in GCP Console → Secret Manager

# ─── Service Accounts ────────────────────────────────────────────────────────

resource "google_service_account" "backend" {
  account_id   = "${local.name_prefix}-backend"
  display_name = "Fomoccs Backend"
}

resource "google_service_account" "pipeline" {
  account_id   = "${local.name_prefix}-pipeline"
  display_name = "Fomoccs Pipeline"
}

# Backend: access secrets + Cloud SQL
resource "google_secret_manager_secret_iam_member" "backend_db_password" {
  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_secret_manager_secret_iam_member" "backend_secret_key" {
  secret_id = google_secret_manager_secret.secret_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_project_iam_member" "backend_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.backend.email}"
}

# Pipeline: access secrets + Cloud SQL
resource "google_secret_manager_secret_iam_member" "pipeline_db_password" {
  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_secret_manager_secret_iam_member" "pipeline_gemini_key" {
  secret_id = google_secret_manager_secret.gemini_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_project_iam_member" "pipeline_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.pipeline.email}"
}

# ─── CI/CD: Workload Identity Federation ─────────────────────────────────────
# Bootstrapped via gcloud CLI (see infrastructure/bootstrap-wif.sh)
# Not managed by Terraform to avoid the chicken-and-egg problem:
# WIF must exist before GitHub Actions can run terraform apply.

# ─── Cloud Run: Backend ──────────────────────────────────────────────────────

resource "google_cloud_run_v2_service" "backend" {
  name     = "${local.name_prefix}-backend"
  location = var.region
  labels   = local.labels

  template {
    service_account = google_service_account.backend.email

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    vpc_access {
      connector = google_vpc_access_connector.connector.id
      egress    = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}/backend:latest"

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      env {
        name  = "ENVIRONMENT"
        value = "production"
      }
      env {
        name  = "CORS_ORIGINS"
        value = "https://storage.googleapis.com,https://fomoccs.vercel.app"
      }
      env {
        name  = "DB_HOST"
        value = google_sql_database_instance.postgres.private_ip_address
      }
      env {
        name  = "DB_NAME"
        value = google_sql_database.fomoccs.name
      }
      env {
        name  = "DB_USER"
        value = google_sql_user.fomoccs.name
      }
      env {
        name = "DB_PASS"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_password.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secret_key.secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get {
          path = "/health"
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# Allow unauthenticated access to the backend API
resource "google_cloud_run_v2_service_iam_member" "backend_public" {
  name     = google_cloud_run_v2_service.backend.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ─── Cloud Run Job: Pipeline ─────────────────────────────────────────────────

resource "google_cloud_run_v2_job" "pipeline" {
  name     = "${local.name_prefix}-pipeline"
  location = var.region
  labels   = local.labels

  template {
    task_count = 1

    template {
      service_account = google_service_account.pipeline.email
      timeout         = "1800s"
      max_retries     = 1

      vpc_access {
        connector = google_vpc_access_connector.connector.id
        egress    = "PRIVATE_RANGES_ONLY"
      }

      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}/pipeline:latest"

        resources {
          limits = {
            cpu    = "2"
            memory = "2Gi"
          }
        }

        env {
          name  = "FOMO_ENV"
          value = "production"
        }
        env {
          name  = "PROD_DB_NAME"
          value = google_sql_database.fomoccs.name
        }
        env {
          name  = "PROD_DB_USER"
          value = google_sql_user.fomoccs.name
        }
        env {
          name  = "PROD_DB_HOST"
          value = google_sql_database_instance.postgres.private_ip_address
        }
        env {
          name = "PROD_DB_PASS"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.db_password.secret_id
              version = "latest"
            }
          }
        }
        env {
          name = "GEMINI_API_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.gemini_api_key.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# ─── Cloud Scheduler: Pipeline trigger ────────────────────────────────────────

resource "google_cloud_scheduler_job" "pipeline_trigger" {
  name     = "${local.name_prefix}-pipeline-daily"
  region   = var.region
  schedule = "0 4 * * *" # Daily at 4:00 AM UTC

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.pipeline.name}:run"

    oauth_token {
      service_account_email = google_service_account.pipeline.email
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "pipeline_run_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.pipeline.email}"
}

# ─── Cloud Storage: Frontend ─────────────────────────────────────────────────

resource "google_storage_bucket" "frontend" {
  name     = "${local.name_prefix}-frontend"
  location = var.region
  labels   = local.labels

  website {
    main_page_suffix = "index.html"
    not_found_page   = "index.html"
  }

  uniform_bucket_level_access = true
  force_destroy               = true
}

resource "google_storage_bucket_iam_member" "frontend_public" {
  bucket = google_storage_bucket.frontend.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}
