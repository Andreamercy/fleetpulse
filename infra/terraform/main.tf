# AWS reference deployment (one cloud, per the brief). The workload is identical on GCP/Azure: swap this
# module for GKE/AKS + managed Kafka/Postgres and change only Helm values (endpoints). NOT applied in the hackathon sandbox.
terraform {
  required_version = ">= 1.6"
  required_providers { aws = { source = "hashicorp/aws", version = "~> 5.0" } }
}
variable "region" { default = "ap-south-1" }   # Mumbai: keeps Indian vehicle data in-region (DPDP)
provider "aws" { region = var.region }

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"
  name = "fleetpulse"
  cidr = "10.20.0.0/16"
  azs             = ["${var.region}a", "${var.region}b", "${var.region}c"]
  private_subnets = ["10.20.1.0/24", "10.20.2.0/24", "10.20.3.0/24"]
  public_subnets  = ["10.20.101.0/24", "10.20.102.0/24", "10.20.103.0/24"]
  enable_nat_gateway = true
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"
  cluster_name    = "fleetpulse"
  cluster_version = "1.30"
  vpc_id          = module.vpc.vpc_id
  subnet_ids      = module.vpc.private_subnets
  eks_managed_node_groups = {
    general = { instance_types = ["m6i.xlarge"], min_size = 3, max_size = 12, desired_size = 3 }
  }
}

resource "aws_msk_cluster" "kafka" {                       # Kafka API: same client code as Redpanda locally
  cluster_name           = "fleetpulse"
  kafka_version          = "3.6.0"
  number_of_broker_nodes = 3
  broker_node_group_info {
    instance_type   = "kafka.m5.large"
    client_subnets  = module.vpc.private_subnets
    storage_info { ebs_storage_info { volume_size = 1000 } }
  }
  encryption_info {
    encryption_in_transit { client_broker = "TLS" }        # TLS in transit
  }
}

resource "aws_db_instance" "pg" {
  identifier              = "fleetpulse"
  engine                  = "postgres"
  engine_version          = "16"
  instance_class          = "db.m6g.large"
  allocated_storage       = 200
  multi_az                = true                           # no single point of failure
  storage_encrypted       = true                           # AES-256 at rest
  manage_master_user_password = true                       # secret lives in Secrets Manager, never in state/code
  db_subnet_group_name    = aws_db_subnet_group.pg.name
  skip_final_snapshot     = false
  final_snapshot_identifier = "fleetpulse-final"
}
resource "aws_db_subnet_group" "pg" { name = "fleetpulse" subnet_ids = module.vpc.private_subnets }
