import type { AnyModel, Host, ResolvedWidget } from "@anywidget/types";

import { modelProxy } from "./model-proxy.js";
import type { ModelState } from "./model.js";
import { parseWidgetRef } from "./widget-ref.js";

export interface WidgetResolver {
  getModel(modelId: string): Promise<AnyModel<ModelState>>;
  getWidget<Exports = unknown>(modelId: string): Promise<ResolvedWidget<Exports>>;
}

export function createHost(resolver: WidgetResolver, parentSignal: AbortSignal): Host {
  return {
    async getModel<State extends ModelState = ModelState>(ref: string): Promise<AnyModel<State>> {
      const model = await resolver.getModel(parseWidgetRef(ref));
      return modelProxy(model, parentSignal) as AnyModel<State>;
    },
    async getWidget<Exports = unknown>(ref: string): Promise<ResolvedWidget<Exports>> {
      const widget = await resolver.getWidget<Exports>(parseWidgetRef(ref));
      return {
        exports: widget.exports,
        async render({ el, signal }) {
          await widget.render({ el, signal: signal ?? parentSignal });
        },
      };
    },
  };
}
