/// <reference types="@raycast/api">

/* 🚧 🚧 🚧
 * This file is auto-generated from the extension's manifest.
 * Do not modify manually. Instead, update the `package.json` file.
 * 🚧 🚧 🚧 */

/* eslint-disable @typescript-eslint/ban-types */

type ExtensionPreferences = {}

/** Preferences accessible in all the extension's commands */
declare type Preferences = ExtensionPreferences

declare namespace Preferences {
  /** Preferences accessible in the `search-artifacts` command */
  export type SearchArtifacts = ExtensionPreferences & {
  /** Claude token (for Ask Haiku) - Optional. Long-lived token from `claude setup-token`, used only by Ask Haiku (⌘↵). Stored encrypted by Raycast. */
  "claudeToken"?: string
}
}

declare namespace Arguments {
  /** Arguments passed to the `search-artifacts` command */
  export type SearchArtifacts = {}
}

