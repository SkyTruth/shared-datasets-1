# platform-shared

Shared platform code and infrastructure for cross-program resources such as user accounts, global geometry layers, and other common services.

## Overview

`platform-shared` contains code and infrastructure-as-code for resources that are used across multiple SkyTruth programs, including Cerulean, 30x30, and future shared consumers.

This repo is intended for foundational platform concerns that should be managed centrally rather than duplicated in program-specific repositories.

Examples include:

- shared user and account management
- global or cross-program geometry layers
- common cloud infrastructure
- reusable platform configuration
- shared services and supporting automation

## Goals

- provide a single source of truth for cross-program platform resources
- reduce duplication across program repos
- make shared infrastructure easier to manage, review, and evolve
- support consistent access control, data definitions, and deployment patterns
- provide a durable home for foundational assets used by multiple systems

## What belongs here

Put something in `platform-shared` if it is:

- used by more than one program or application
- foundational platform infrastructure or configuration
- better managed centrally than copied into individual repos
- expected to remain shared over time

Examples:

- identity and user/account provisioning
- org-wide roles, groups, or permissions scaffolding
- global geometry or boundary layers used across programs
- shared buckets, databases, secrets wiring, or service integrations
- common deployment modules or environment bootstrapping

## What does not belong here

The following usually belong in program-specific repos instead:

- business logic specific to Cerulean, 30x30, or another single program
- one-off analysis code
- product-specific APIs, UI code, or workflows
- resources that are only consumed by one system
- experimental code that is not yet clearly shared

## Repository structure

The exact structure may evolve, but a typical layout might look like:

```text
.
├── iac/                # Terraform, Pulumi, CDK, or other infra definitions
├── src/                # Shared application/library code
├── scripts/            # Operational and maintenance scripts
├── config/             # Shared configuration and templates
├── data/               # Versioned shared metadata or lightweight artifacts
└── docs/               # Design notes, runbooks, and architecture docs
