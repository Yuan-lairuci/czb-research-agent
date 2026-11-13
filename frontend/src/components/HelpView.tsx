import { Alert, Card, Col, Row, Tag, Typography } from 'antd'

const { Title, Paragraph, Text } = Typography

const MODULES = [
  { key: '文献库', desc: '上传 PDF → 解析入库 → 去重扫描 → 向量索引，一篇文献的完整生命周期管理。' },
  { key: '阅读器+翻译', desc: 'PDF/解析文本双视图阅读；划词翻译（流式输出）、整篇翻译与中英对照同步滚动。' },
  { key: 'AI 问答', desc: '基于文献分块索引的可信问答：答案带页码引用卡片，点击引用可跳回原文。' },
  { key: '检索推荐', desc: 'Semantic Scholar / arXiv 双源检索、一键入库、按论文推荐、arXiv 版本追踪、赞踩反馈。' },
  { key: '引用图谱', desc: '文献互引图谱构建 → 中心性分析（PageRank）→ 综述生成（[n] 标注可溯源）→ 未来工作时间线。' },
  { key: '写作 Copilot', desc: '论文提纲生成、参考文献核验（已验证/丢弃分栏）、TikZ/Graphviz/Mermaid 图表代码、CSV 图表渲染。' },
  { key: '帮助', desc: '本页：功能地图、演示路线与降级说明。' },
]

export default function HelpView() {
  return (
    <div className="view view-help">
      <Card className="help-hero">
        <Title level={3}>科研助手 Agent · 使用帮助</Title>
        <Paragraph type="secondary">
          面向科研场景的一站式 AI 助手：文献管理、可信问答、检索推荐、引用图谱与写作辅助。
          所有 AI 生成内容均带来源标注，可追溯到原文页码与文献条目。
        </Paragraph>
        <Paragraph>
          <Text strong>快速上手：</Text>
          在「文献库」上传或选择一篇文献 → 「阅读器+翻译」边读边译 → 「AI 问答」追问细节 →
          「检索推荐」扩充文献池 → 「引用图谱」看领域结构 → 「写作 Copilot」产出初稿素材。
        </Paragraph>
      </Card>

      <Title level={5} className="help-section-title">功能地图</Title>
      <Row gutter={[12, 12]}>
        {MODULES.map((m) => (
          <Col key={m.key} xs={24} sm={12} lg={8}>
            <Card size="small" className="help-card" title={<Tag color="blue">{m.key}</Tag>}>
              <Paragraph type="secondary" className="help-card-desc">{m.desc}</Paragraph>
            </Card>
          </Col>
        ))}
      </Row>

      <Title level={5} className="help-section-title">演示路线（约 5 分钟）</Title>
      <Card size="small" className="help-route">
        <ol className="help-list">
          <li><Text strong>文献库</Text>：查看 21 篇内置 NLP/LLM 经典文献，演示去重扫描与向量索引。</li>
          <li><Text strong>阅读器+翻译</Text>：选中英文段落划词翻译（流式）；整篇翻译生成中英对照，双向滚动同步。</li>
          <li><Text strong>AI 问答</Text>：提问"LoRA 相比全量微调的优势"，点击答案引用卡片跳回原文页。</li>
          <li><Text strong>检索推荐</Text>：检索 "attention is all you need"，一键入库；按论文推荐相关工作并赞踩。</li>
          <li><Text strong>引用图谱</Text>：一键建图 → 中心性分析（基石/边缘着色）→ 生成综述（点击 [n] 溯源）→ 未来工作时间线。</li>
          <li><Text strong>写作 Copilot</Text>：生成提纲六段卡片；参考文献核验分栏；粘贴 CSV 渲染图表并生成 Figure Caption。</li>
        </ol>
      </Card>

      <Title level={5} className="help-section-title">技术栈</Title>
      <Card size="small" className="help-route">
        <Paragraph className="help-list">
          <Tag>React 18</Tag><Tag>TypeScript</Tag><Tag>Vite</Tag><Tag>Ant Design v6</Tag>
          <Tag>PDF.js</Tag><Tag>ECharts</Tag><Tag>FastAPI</Tag><Tag>Semantic Scholar</Tag><Tag>arXiv</Tag>
        </Paragraph>
      </Card>

      <Alert
        className="help-degrade"
        type="info"
        showIcon
        title="降级说明"
        description="未配置大模型密钥时，AI 生成类功能显示「模型服务未配置」占位，其余功能（文献管理、检索、图谱统计、图表渲染、参考文献核验的来源部分）仍可正常使用；综述与中心性解读在无模型时自动切换为模板生成，保证演示不中断。"
      />
    </div>
  )
}
