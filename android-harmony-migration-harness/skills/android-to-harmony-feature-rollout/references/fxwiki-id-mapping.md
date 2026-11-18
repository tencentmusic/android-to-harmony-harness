# Harness 与知识库身份映射

| 标识 | 用途 |
| --- | --- |
| 合同 feature_id | 本次迁移任务的稳定标识，run 目录由调用者指定 |
| 合同 capability.id | 当前任务内能力标识 |
| knowledge capability | catalog 中稳定的知识身份，对应唯一 canonical 包 |
| Profile project_id | CodeGraph 工程身份 |

同一知识能力可以有多次迁移任务。Agent 在项目知识材料中记录任务能力到知识库的映射，
以 project_profile.knowledge 引用，并在开发交接中记录实际知识回灌依据。

新建知识包前读取 catalog，核对 capability、别名、标题、完整路径和 canonical 路由。
命中已有包则复用；不同标识互相冲突时先查证并解决。索引缺失不能作为“无已有包”的证据。
该查重由 Agent 按知识库规范执行，Harness start 不提供知识库参数或自动改写知识身份。
