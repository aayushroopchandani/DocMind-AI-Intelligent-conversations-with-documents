import {
  LogLevel,
  Univer,
  type IUniverConfig,
  type Plugin,
  type PluginCtor,
} from "@univerjs/core";
import { FUniver } from "@univerjs/core/facade";

/**
 * Minimal local equivalent of `createUniver` from `@univerjs/presets`.
 *
 * The `@univerjs/presets` meta-package depends on every preset — including
 * `@univerjs/preset-sheets-advanced`, which transitively installs
 * `@univerjs-pro/*` packages. This project must stay on open-source Univer
 * only, so we depend on the clean `@univerjs/preset-sheets-core` package
 * directly and reproduce the (small, Apache-2.0) helper here: construct a
 * `Univer`, register the preset's plugins once each, and expose the Facade.
 */

type PresetPlugin =
  | PluginCtor<Plugin>
  | [PluginCtor<Plugin>, ConstructorParameters<PluginCtor<Plugin>>[0]];

export interface UniverPreset {
  plugins: PresetPlugin[];
}

export interface CreateUniverOptions extends Partial<IUniverConfig> {
  presets: UniverPreset[];
}

export function createUniver(options: CreateUniverOptions): {
  univer: Univer;
  univerAPI: FUniver;
} {
  const { presets, ...config } = options;

  const univer = new Univer({ logLevel: LogLevel.WARN, ...config });

  // Later presets win on plugin-name collisions, mirroring the official
  // helper, and each plugin registers exactly once.
  const registry = new Map<
    string,
    { plugin: PluginCtor<Plugin>; pluginOptions: unknown }
  >();
  for (const preset of presets) {
    for (const entry of preset.plugins) {
      const [plugin, pluginOptions] = Array.isArray(entry)
        ? entry
        : [entry, undefined];
      registry.delete(plugin.pluginName);
      registry.set(plugin.pluginName, { plugin, pluginOptions });
    }
  }
  registry.forEach(({ plugin, pluginOptions }) => {
    univer.registerPlugin(plugin, pluginOptions);
  });

  const univerAPI = FUniver.newAPI(univer);
  return { univer, univerAPI };
}
