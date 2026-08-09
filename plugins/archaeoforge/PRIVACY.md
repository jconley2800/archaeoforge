# ArchaeoForge plugin privacy policy

Effective: August 9, 2026

## Summary

The ArchaeoForge Codex plugin is a skills-only, locally executed workflow package. It has no hosted service, user database, analytics, advertising, or telemetry, and the publisher does not receive project data through the plugin.

## Local data

When invoked, the plugin may direct Codex and the locally installed ArchaeoForge CLI to read project configuration, licensed research sources, evidence records, geometry, renders, and provenance files selected by the user. It may create or update local project files and generated outputs within the user's authorized workspace.

Codex may receive file contents, absolute local paths, filenames, and file-presence metadata needed to inspect and operate the selected project. ArchaeoForge provenance records may retain project-relative paths, cryptographic hashes, prompt hashes, model and response metadata, and reviewer names or notes entered by the user. These records remain in the user's project unless the user copies or shares them.

## Optional OpenAI processing

The workflow can use OpenAI services in three user-controlled situations:

1. Codex's built-in image generation transforms a selected base render into a candidate image.
2. Optional ArchaeoForge extraction sends selected source content to the OpenAI API using the user's API key.
3. Optional unattended finishing or strict geometry auditing sends selected prompts and images to the OpenAI API using the user's API key.

The plugin itself does not receive this content. Processing and retention by OpenAI are governed by the user's applicable OpenAI terms, privacy policy, workspace controls, and API data settings.

## Retention and deletion

The publisher does not operate a server that stores plugin data and therefore has no publisher-side account or project-data deletion process. Local inputs, generated files, caches, and provenance remain under the user's control and follow the user's normal filesystem, backup, and repository retention practices. Users can delete local artifacts directly, subject to any copies in backups or version control. Content sent to OpenAI follows the retention and deletion controls of the OpenAI product and account used for that operation.

## Credentials

The plugin does not ask users to paste API keys into chat and does not transmit credentials to the publisher. ArchaeoForge reads only the project API key needed for an explicitly requested API operation and pins its supported API client to the official OpenAI endpoint.

## User responsibilities

Users are responsible for permission to process source materials, personal data, culturally sensitive information, restricted site information, and third-party content. Users should not provide content they are not authorized to use.

## Changes and contact

Material changes will be documented in the public repository. Privacy questions and reports may be filed at <https://github.com/jconley2800/archaeoforge/issues>.
