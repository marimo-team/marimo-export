import type { AnyModel, Host, ResolvedWidget } from "@anywidget/types";

import { combineAbortSignals, throwIfAborted } from "./abort.js";
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
      throwIfAborted(parentSignal, "AnyWidget parent view was disposed.");
      const model = await resolver.getModel(parseWidgetRef(ref));
      throwIfAborted(parentSignal, "AnyWidget parent view was disposed.");
      return modelProxy(model, parentSignal) as AnyModel<State>;
    },
    async getWidget<Exports = unknown>(ref: string): Promise<ResolvedWidget<Exports>> {
      throwIfAborted(parentSignal, "AnyWidget parent view was disposed.");
      const widget = await resolver.getWidget<Exports>(parseWidgetRef(ref));
      throwIfAborted(parentSignal, "AnyWidget parent view was disposed.");
      return {
        exports: widget.exports,
        async render({ el, signal }) {
          const renderSignal =
            signal === undefined || signal === parentSignal
              ? parentSignal
              : combineAbortSignals([parentSignal, signal]);
          throwIfAborted(renderSignal, "AnyWidget parent view was disposed.");
          await widget.render({
            el,
            signal: renderSignal,
          });
        },
      };
    },
  };
}
