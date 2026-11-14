# ModelTrace: 基于离散概率测度的黑盒大模型路由归因与指纹测谎理论

## 1. 核心问题背景
在当今多模型混合路由（LLM Gateways）、聚合反代（API Resellers）与套壳大模型场景中，服务商经常宣称提供顶尖大模型（如 Claude 3.5 Sonnet 或 GPT-4o），但实际在后台通过降级路由（Silent Fallback）偷换为较便宜的小模型（如 DeepSeek-Lite, Qwen-Turbo, Claude Haiku），以节省高昂推理成本。传统文本分析（如文本困惑度 Perplexity 或语义比对）易受 Temperature、Top-P 和 System Prompt 影响，无法作为确定性技术抗辩证据。

## 2. 统计指纹假设 (The Statistical Fingerprint Hypothesis)
不同大模型由于分词器（Tokenizer BPE/Byte-level BPE）、预训练语料偏置（Training Corpus Distribution）、对齐强化学习（RLHF/DPO）以及权重初始化随机性的差异，其在处理**离散伪随机序列生成**任务时，会展现出模型家族特有的**固有统计偏好（Intrinsic Statistical Bias）**。

当向模型提出挑战：
> *"从 [1, 355] 之间随机挑选并输出 250 个不重复的整数"*

大模型并未真正执行真正的物理随机数生成器（TRNG），而是依据其上下文注意力权重与自回归解码逻辑展开选择。其生成的数字序列在 `[1, 355]` 离散空间上的频数分布直方图 $P = (p_1, p_2, \dots, p_{355})$ 构成了该模型独一无二的“血统指纹”。

## 3. 数学度量算法: 海灵格距离 (Hellinger Distance)
相比于对零概率项敏感的 KL 散度（Kullback-Leibler Divergence），海灵格距离满足三角不等式，并具备严格的对称性与有界性 ($0 \le H \le 1$)：

$$H(P, Q) = \frac{1}{\sqrt{2}} \sqrt{ \sum_{i=1}^{k} \left( \sqrt{P_i} - \sqrt{Q_i} \right)^2 }$$

其中：
- $P$: 待检测 API 输出序列经过标准化后的经验概率向量；
- $Q$: 官方基准模型在数千次采样后收敛的先验特征向量（存储于 `unified_bank.json`）；
- $k = 355$: 采样空间维度。

## 4. 判定与归因分类
1. **模型相似度得分**:
   $$S_m = 1 - H(P, Q_m)$$
2. **家族聚合置信度 (Family Aggregation)**:
   将同一家族模型（如 Claude 家族所有变体）的相似度加权聚合，通过带温度平滑因子的 Softmax 转换为后验概率：
   $$P(\text{Family}_j | P) = \frac{\exp(S_j / \tau)}{\sum_k \exp(S_k / \tau)}$$
3. **输出置信度结论**:
   输出最高概率的模型家族与具体版本，判定可信度可达 95% 以上。
