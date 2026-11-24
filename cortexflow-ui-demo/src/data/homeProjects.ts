/** Fictional project overview records for the 2026-11-24 design. No backend. */
export interface HomeProject {
  id: string;
  name: string;
  description: string;
  modifiedAt: string;
  lastTask: string | null;
}
export const HOME_MOCK_DATE = "2026-11-24";
export const homeProjects: HomeProject[] = [
  {
    id: "cortex",
    name: "CortexFlow",
    description: "多 Agent 协作与项目上下文工作台。",
    modifiedAt: "2026-11-24",
    lastTask: "整理桌面客户端结构",
  },
  {
    id: "loongarch",
    name: "LoongArch-CPU-2027",
    description: "龙芯架构 CPU 设计与验证项目。",
    modifiedAt: "2026-11-23",
    lastTask: "补充流水线异常处理测试",
  },
  {
    id: "uxuescript",
    name: "uxuescript",
    description: "学习通课程自动化客户端。",
    modifiedAt: "2026-11-22",
    lastTask: "修复课程列表加载逻辑",
  },
  {
    id: "nuwanda-space",
    name: "nuwanda.space",
    description: "个人博客与项目展示站点。",
    modifiedAt: "2026-11-21",
    lastTask: "调整首页文章列表样式",
  },
  {
    id: "writing-reading",
    name: "writing_reading",
    description: "写作、阅读与知识整理。",
    modifiedAt: "2026-10-08",
    lastTask: "整理本周阅读笔记",
  },
];
