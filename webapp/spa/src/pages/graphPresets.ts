import type { RuleGraph } from "../api/types";

/* 空白画布预设：新规则从告警节点开始，用户可按需搭建检测逻辑。 */

export interface GraphPreset {
  key: string;
  title: string;
  desc: string;
  graph: RuleGraph;
}

export const BLANK_PRESET: GraphPreset = {
  key: "blank",
  title: "空白画布",
  desc: "从零开始：从左侧积木库添加节点、连线，搭建自己的检测逻辑。",
  graph: {
    nodes: [{ id: "n1", type: "alert", params: {} }],
    edges: [],
  },
};
