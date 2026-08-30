import type { RuleGraph } from "../api/types";

/* 预设画廊（契约 docs/RULE_GRAPH_DESIGN.md §7）：
 * 8 个预置画布 + 1 个空白画布。
 * 全部预设均为真实判定；no_helmet 使用 class_covering（装备覆盖检查）节点表达。 */

export interface GraphPreset {
  key: string;
  title: string;
  desc: string;
  graph: RuleGraph;
}

const CONF = { min_confidence: 0.5 };

export const GRAPH_PRESETS: GraphPreset[] = [
  {
    key: "cat_at_door",
    title: "门口有猫",
    desc: "画面里出现猫立即告警。可在画布参数中把类别换成任意想盯防的动物或物品。",
    graph: {
      nodes: [
        { id: "n1", type: "class_present", params: { classes: ["cat"], ...CONF } },
        { id: "n2", type: "alert", params: {} },
      ],
      edges: [{ from: "n1", to: "n2" }],
    },
  },
  {
    key: "person_leave",
    title: "人员离开画面（离岗）",
    desc: "有人值守时正常；画面里人员连续 10 秒消失即判定离岗告警。",
    graph: {
      nodes: [
        { id: "n1", type: "class_present", params: { classes: ["person"], ...CONF } },
        { id: "n2", type: "not", params: {} },
        { id: "n3", type: "duration", params: { seconds: 10 } },
        { id: "n4", type: "alert", params: {} },
      ],
      edges: [
        { from: "n1", to: "n2" },
        { from: "n2", to: "n3" },
        { from: "n3", to: "n4" },
      ],
    },
  },
  {
    key: "person_near_door",
    title: "有人靠近门口",
    desc: "人员的中心点落入「门口区域」即告警。区域可在「在指定区域内」节点里重新框选。",
    graph: {
      nodes: [
        { id: "n1", type: "class_present", params: { classes: ["person"], ...CONF } },
        {
          id: "n2",
          type: "in_zone",
          params: { zones: [{ x: 0.33, y: 0.22, w: 0.34, h: 0.56 }] },
        },
        { id: "n3", type: "alert", params: {} },
      ],
      edges: [
        { from: "n1", to: "n2" },
        { from: "n2", to: "n3" },
      ],
    },
  },
  {
    key: "fence_intrusion",
    title: "闯入围墙",
    desc: "有人进入「围墙区域」立即告警。建议把区域框在画面下沿的围栏/周界位置。",
    graph: {
      nodes: [
        { id: "n1", type: "class_present", params: { classes: ["person"], ...CONF } },
        {
          id: "n2",
          type: "in_zone",
          params: { zones: [{ x: 0.05, y: 0.55, w: 0.9, h: 0.42 }] },
        },
        { id: "n3", type: "alert", params: {} },
      ],
      edges: [
        { from: "n1", to: "n2" },
        { from: "n2", to: "n3" },
      ],
    },
  },
  {
    key: "danger_dwell",
    title: "危险区域逗留",
    desc: "人员进入「危险区域」并持续停留超过 30 秒才告警，短暂路过不会触发。",
    graph: {
      nodes: [
        { id: "n1", type: "class_present", params: { classes: ["person"], ...CONF } },
        {
          id: "n2",
          type: "in_zone",
          params: { zones: [{ x: 0.55, y: 0.3, w: 0.4, h: 0.5 }] },
        },
        { id: "n3", type: "duration", params: { seconds: 30 } },
        { id: "n4", type: "alert", params: {} },
      ],
      edges: [
        { from: "n1", to: "n2" },
        { from: "n2", to: "n3" },
        { from: "n3", to: "n4" },
      ],
    },
  },
  {
    key: "zone_cleared",
    title: "区域清空 / 物品被盗",
    desc: "包裹类物品（默认 backpack / handbag / suitcase）连续 10 秒从画面消失即告警，适合看管财物。",
    graph: {
      nodes: [
        {
          id: "n1",
          type: "class_present",
          params: { classes: ["backpack", "handbag", "suitcase"], ...CONF },
        },
        { id: "n2", type: "not", params: {} },
        { id: "n3", type: "duration", params: { seconds: 10 } },
        { id: "n4", type: "alert", params: {} },
      ],
      edges: [
        { from: "n1", to: "n2" },
        { from: "n2", to: "n3" },
        { from: "n3", to: "n4" },
      ],
    },
  },
  {
    key: "smoking",
    title: "吸烟",
    desc: "画面检出香烟（cigarette）且贴近人员即告警；地上的烟头不触发。",
    graph: {
      nodes: [
        { id: "n1", type: "class_present", params: { classes: ["cigarette"], ...CONF } },
        { id: "n2", type: "near_class", params: { ref_classes: ["person"], margin: 0.3 } },
        { id: "n3", type: "alert", params: {} },
      ],
      edges: [
        { from: "n1", to: "n2" },
        { from: "n2", to: "n3" },
      ],
    },
  },
  {
    key: "no_helmet",
    title: "未戴安全帽",
    desc: "人员在场且未戴安全帽（或检出无帽类）即告警。可调覆盖比例与一票否决类别。",
    graph: {
      nodes: [
        { id: "n1", type: "class_present", params: { classes: ["person"], min_confidence: 0 } },
        { id: "ng", type: "class_covering", params: { classes: ["hardhat"], ref_classes: ["person"], coverage_ratio: 0.5, min_confidence: 0 } },
        { id: "nn", type: "not", params: {} },
        { id: "nv", type: "class_present", params: { classes: ["no-hardhat"], min_confidence: 0 } },
        { id: "no", type: "or", params: {} },
        { id: "na", type: "and", params: {} },
        { id: "al", type: "alert", params: {} },
      ],
      edges: [
        { from: "ng", to: "nn" },
        { from: "nn", to: "no" },
        { from: "nv", to: "no" },
        { from: "no", to: "na" },
        { from: "n1", to: "na" },
        { from: "na", to: "al" },
      ],
    },
  },
];

/* 空白画布：预置一个「告警」节点作为终点，从积木库添加来源节点连线即可 */
export const BLANK_PRESET: GraphPreset = {
  key: "blank",
  title: "空白画布",
  desc: "从零开始：从左侧积木库添加节点、连线，搭建自己的检测逻辑。",
  graph: {
    nodes: [{ id: "n1", type: "alert", params: {} }],
    edges: [],
  },
};
