export type HostDiagnosticCode =
  | 'CAPABILITY_MISSING'
  | 'CAPABILITY_UNKNOWN'
  | 'CONFIG_INVALID'
  | 'DEPENDENCY_CYCLE'
  | 'DEPENDENCY_MISSING'
  | 'DEPENDENCY_VERSION'
  | 'PLUGIN_DUPLICATE'
  | 'PLUGIN_NOT_FOUND'
  | 'PLUGIN_START_FAILED'
  | 'PROVIDER_CONFLICT'
  | 'PROVIDER_UNDECLARED'

export interface HostDiagnostic {
  code: HostDiagnosticCode
  message: string
  pointer?: string
  plugin?: string
  scope?: string
  hint?: string
  details?: unknown
}

export class HostDiagnosticError extends Error implements HostDiagnostic {
  readonly code: HostDiagnosticCode
  readonly pointer?: string
  readonly plugin?: string
  readonly scope?: string
  readonly hint?: string
  readonly details?: unknown

  constructor(diagnostic: HostDiagnostic, options?: ErrorOptions) {
    super(diagnostic.message, options)
    this.name = 'HostDiagnosticError'
    this.code = diagnostic.code
    if (diagnostic.pointer !== undefined) this.pointer = diagnostic.pointer
    if (diagnostic.plugin !== undefined) this.plugin = diagnostic.plugin
    if (diagnostic.scope !== undefined) this.scope = diagnostic.scope
    if (diagnostic.hint !== undefined) this.hint = diagnostic.hint
    if (diagnostic.details !== undefined) this.details = diagnostic.details
  }

  toJSON(): HostDiagnostic {
    return {
      code: this.code,
      message: this.message,
      ...(this.pointer === undefined ? {} : { pointer: this.pointer }),
      ...(this.plugin === undefined ? {} : { plugin: this.plugin }),
      ...(this.scope === undefined ? {} : { scope: this.scope }),
      ...(this.hint === undefined ? {} : { hint: this.hint }),
      ...(this.details === undefined ? {} : { details: this.details }),
    }
  }
}
