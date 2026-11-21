import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Tag,
  Tree,
  Typography,
  message,
} from 'antd'
import type { DataNode } from 'antd/es/tree'
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import {
  commitSkillZip,
  createSkillFile,
  deleteSkillFile,
  forkSkillVersion,
  getSkillFileContent,
  getSkillFileTree,
  previewSkillZip,
  publishSkillVersion,
  renameSkillFile,
  updateSkillFile,
} from '../../api/client'
import type { SkillFileSummary, SkillZipPreview } from '../../api/client'
import { useTenantContext } from '../management/TenantContext'

const { Paragraph, Text, Title } = Typography

interface FileContent {
  path: string
  mime_type: string
  size_bytes: number
  sha256: string | null
  content_text: string | null
  content_base64: string | null
}

interface FileTreeNode extends DataNode {
  filePath?: string
}

function buildTree(files: SkillFileSummary[]): FileTreeNode[] {
  type MutableNode = { key: string; title: string; filePath?: string; children: MutableNode[] }
  const roots: MutableNode[] = []
  for (const file of files) {
    const segments = file.path.split('/')
    let children = roots
    let pathPrefix = ''
    segments.forEach((segment, index) => {
      pathPrefix = pathPrefix ? `${pathPrefix}/${segment}` : segment
      let node = children.find((item) => item.title === segment)
      if (!node) {
        node = { key: index === segments.length - 1 ? file.path : `dir:${pathPrefix}`, title: segment, children: [] }
        children.push(node)
      }
      if (index === segments.length - 1) node.filePath = file.path
      children = node.children
    })
  }
  const convert = (node: MutableNode): FileTreeNode => ({
    key: node.key,
    title: node.title,
    filePath: node.filePath,
    isLeaf: Boolean(node.filePath),
    children: node.children.map(convert),
  })
  return roots.map(convert)
}

/** Skill 精确版本的文件树和编辑工作区，REFERENCE/已发布版本只读。 */
export function SkillFileWorkspacePage() {
  const { employeeId = '', skillId = '', version: versionParam = '1' } = useParams()
  const version = Number(versionParam)
  const returnPath = employeeId
    ? `/enterprise/employees/${encodeURIComponent(employeeId)}`
    : '/enterprise/resources'
  const { fakeUser, currentIdentity } = useTenantContext()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const canEdit = currentIdentity.roleName === '管理员'
  const [selectedPath, setSelectedPath] = useState<string>()
  const [content, setContent] = useState<FileContent>()
  const [draftText, setDraftText] = useState('')
  const [draftBase64, setDraftBase64] = useState('')
  const [dirty, setDirty] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm] = Form.useForm<{ file_path: string; content_text: string }>()
  const [renameOpen, setRenameOpen] = useState(false)
  const [renameForm] = Form.useForm<{ new_file_path: string }>()
  const [zipFile, setZipFile] = useState<File>()
  const [zipStrategy, setZipStrategy] = useState<'replace' | 'merge'>('replace')
  const [zipPreview, setZipPreview] = useState<SkillZipPreview>()

  const treeQuery = useQuery({
    queryKey: ['skill-files', fakeUser, skillId, version],
    queryFn: () => getSkillFileTree(skillId, version, fakeUser),
    enabled: Boolean(skillId && Number.isFinite(version)),
  })
  const contentQuery = useQuery({
    queryKey: ['skill-file-content', fakeUser, skillId, version, selectedPath],
    queryFn: () => getSkillFileContent(skillId, version, selectedPath!, fakeUser),
    enabled: Boolean(selectedPath),
  })
  const tree = treeQuery.data
  const files = tree?.files ?? []
  const nodes = useMemo(() => buildTree(files), [files])
  const readOnly = Boolean(tree?.read_only) || !canEdit
  const archiveInputId = 'skill-package-zip-input'

  useEffect(() => {
    if (!tree || selectedPath) return
    const root = tree.files.find((file) => file.path === tree.entry_path) ?? tree.files[0]
    if (root) setSelectedPath(root.path)
  }, [selectedPath, tree])

  useEffect(() => {
    if (!contentQuery.data) return
    setContent(contentQuery.data)
    setDraftText(contentQuery.data.content_text ?? '')
    setDraftBase64(contentQuery.data.content_base64 ?? '')
    setDirty(false)
  }, [contentQuery.data])

  const invalidateFiles = async (nextPath?: string) => {
    await queryClient.invalidateQueries({ queryKey: ['skill-files', fakeUser, skillId, version] })
    await queryClient.invalidateQueries({ queryKey: ['skill-file-content', fakeUser, skillId, version] })
    setDirty(false)
    if (nextPath) setSelectedPath(nextPath)
  }

  const saveMutation = useMutation({
    mutationFn: () => updateSkillFile(
      skillId,
      version,
      {
        file_path: selectedPath!,
        expected_sha256: content!.sha256!,
        ...(content?.content_text !== null
          ? { content_text: draftText }
          : { content_base64: draftBase64 }),
      },
      fakeUser,
    ),
    onSuccess: async () => {
      await invalidateFiles()
      message.success('Skill 文件已保存')
    },
  })

  const createMutation = useMutation({
    mutationFn: (variables: { skillId: string; version: number; input: { file_path: string; content_text: string }; fakeUser: string }) =>
      createSkillFile(variables.skillId, variables.version, variables.input, variables.fakeUser),
    onSuccess: async (result) => {
      const newPath = String(result.entry_path === 'SKILL.md' ? 'SKILL.md' : createForm.getFieldValue('file_path'))
      await invalidateFiles(newPath)
      setCreateOpen(false)
      createForm.resetFields()
      message.success('文件已创建')
    },
  })

  const renameMutation = useMutation({
    mutationFn: (newPath: string) => renameSkillFile(
      skillId,
      version,
      { file_path: selectedPath!, new_file_path: newPath, expected_sha256: content!.sha256! },
      fakeUser,
    ),
    onSuccess: async () => {
      const nextPath = renameForm.getFieldValue('new_file_path')
      await invalidateFiles(nextPath)
      setRenameOpen(false)
      renameForm.resetFields()
      message.success('文件路径已更新')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteSkillFile(skillId, version, selectedPath!, content!.sha256!, fakeUser),
    onSuccess: async () => {
      const rootPath = tree?.entry_path ?? 'SKILL.md'
      await invalidateFiles(rootPath)
      message.success('文件已删除')
    },
  })

  const previewMutation = useMutation({
    mutationFn: (file: File) => previewSkillZip(skillId, version, file, zipStrategy, fakeUser),
    onSuccess: (preview) => setZipPreview(preview),
  })

  const commitMutation = useMutation({
    mutationFn: () => {
      if (!zipFile || !zipPreview) throw new Error('请先预览 ZIP 文件')
      return commitSkillZip(skillId, version, zipPreview.staging_id, zipFile, fakeUser)
    },
    onSuccess: async () => {
      await invalidateFiles('SKILL.md')
      setZipPreview(undefined)
      setZipFile(undefined)
      message.success('Skill 文件包已导入')
    },
  })

  const forkMutation = useMutation({
    mutationFn: () => forkSkillVersion(skillId, version, fakeUser),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ['skill-files', fakeUser, skillId] })
      message.success(`已创建 Skill 草稿 v${created.version}`)
      navigate(employeeId
        ? `/enterprise/employees/${encodeURIComponent(employeeId)}/skills/${encodeURIComponent(skillId)}/versions/${created.version}/files`
        : `/enterprise/resources/skills/${encodeURIComponent(skillId)}/versions/${created.version}/files`)
    },
  })

  const publishMutation = useMutation({
    mutationFn: () => publishSkillVersion(skillId, version, fakeUser),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['skill-files', fakeUser, skillId, version] })
      await queryClient.invalidateQueries({ queryKey: ['skills', fakeUser] })
      message.success(`Skill v${version} 已发布`)
    },
  })

  const onSelectFile = (path: string) => {
    if (dirty && !window.confirm('当前文件有未保存修改，切换文件会放弃这些修改。继续吗？')) return
    setDirty(false)
    setSelectedPath(path)
    setContent(undefined)
  }

  const renderFileDetails = () => {
    if (!selectedPath) return <Empty description="选择一个文件查看内容" />
    if (contentQuery.isLoading || !content) return <div className="management-loading">正在读取文件…</div>
    const isText = content.content_text !== null
    return (
      <>
        <div className="file-editor-toolbar">
          <div>
            <Title level={5}>{selectedPath}</Title>
            <Text type="secondary">{content.mime_type} · {content.size_bytes} bytes · SHA-256 {content.sha256?.slice(0, 12)}</Text>
          </div>
          {!readOnly && (
            <Space wrap>
              {dirty && <Button type="primary" loading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>保存文件</Button>}
              {selectedPath !== 'SKILL.md' && <Button onClick={() => { renameForm.setFieldsValue({ new_file_path: selectedPath }); setRenameOpen(true) }}>重命名</Button>}
              {selectedPath !== 'SKILL.md' && (
                <Popconfirm
                  title="删除这个文件？"
                  description="该操作只修改当前 DRAFT Skill 清单。"
                  okText="确认删除"
                  cancelText="取消"
                  onConfirm={() => deleteMutation.mutate()}
                >
                  <Button danger loading={deleteMutation.isPending}>删除</Button>
                </Popconfirm>
              )}
            </Space>
          )}
        </div>
        {isText ? (
          <Input.TextArea
            aria-label="文件正文"
            className="skill-file-editor"
            value={draftText}
            readOnly={readOnly}
            onChange={(event) => {
              setDraftText(event.target.value)
              setDirty(true)
            }}
            spellCheck={false}
          />
        ) : (
          <>
            <Alert type="info" message="二进制文件以 Base64 显示；编辑前请确认内容格式。" />
            <Input.TextArea
              aria-label="文件 Base64 正文"
              className="skill-file-editor"
              value={draftBase64}
              readOnly={readOnly}
              onChange={(event) => {
                setDraftBase64(event.target.value)
                setDirty(true)
              }}
              spellCheck={false}
            />
          </>
        )}
        {saveMutation.isError && <Alert className="file-action-error" type="error" message={(saveMutation.error as Error).message} />}
        {deleteMutation.isError && <Alert className="file-action-error" type="error" message={(deleteMutation.error as Error).message} />}
      </>
    )
  }

  if (treeQuery.isLoading) return <div className="management-loading">正在加载 Skill 文件树…</div>
  if (treeQuery.isError || !tree) {
    return <Alert type="error" showIcon message="文件工作区加载失败" description={(treeQuery.error as Error).message} />
  }

  return (
    <section className="management-page" aria-label="Skill 文件工作区">
      <div className="management-page-header">
        <div>
          <Link to={returnPath}>← 返回{employeeId ? '员工详情' : '资源中心'}</Link>
          <Text className="management-eyebrow">
            {employeeId ? `员工 Skill / v${version}` : `技能资源 / v${version}`}
          </Text>
          <Space align="center">
            <Title level={2}>Skill 文件工作区</Title>
            <Tag color={readOnly ? 'default' : 'blue'}>{readOnly ? '只读' : tree.status}</Tag>
            <Tag>{tree.content_format}</Tag>
          </Space>
          <Text type="secondary">入口：{tree.entry_path ?? 'INLINE instruction'} · 清单摘要：{tree.file_manifest_sha256 ?? '尚未生成'}</Text>
        </div>
        {!readOnly && (
          <Space wrap>
            <Popconfirm
              title={`发布 Skill v${version}？`}
              description="发布后该版本及其文件清单不可修改。"
              okText="确认发布"
              cancelText="取消"
              onConfirm={() => publishMutation.mutate()}
            >
              <Button type="primary" loading={publishMutation.isPending}>发布 Skill</Button>
            </Popconfirm>
            <Button onClick={() => setCreateOpen(true)}>新建文件</Button>
            <label className="zip-upload-button" htmlFor={archiveInputId}>上传 ZIP</label>
            <input
              id={archiveInputId}
              aria-label="上传 Skill ZIP"
              type="file"
              accept=".zip,application/zip"
              onChange={(event) => {
                const selected = event.target.files?.[0]
                if (!selected) return
                if (selected.size > 20 * 1024 * 1024) {
                  message.error('ZIP 压缩包超过 20 MiB 上限')
                  event.target.value = ''
                  return
                }
                setZipFile(selected)
                previewMutation.mutate(selected)
                event.target.value = ''
              }}
              hidden
            />
          </Space>
        )}
        {canEdit && tree.status === 'PUBLISHED' && tree.fork_allowed && (
          <Button loading={forkMutation.isPending} onClick={() => forkMutation.mutate()}>
            基于此版本创建 Skill 草稿
          </Button>
        )}
      </div>
      {readOnly && <Alert className="file-readonly-alert" type="info" showIcon message={
        !canEdit
          ? '当前身份只能查看 Skill 文件'
          : tree.status === 'PUBLISHED' && tree.fork_allowed
            ? '已发布 Skill 版本只读；需要修改时请创建新草稿版本。'
            : tree.source_mode === 'REFERENCE'
              ? 'Hub REFERENCE Skill 只读；修改前请先 COPY 到本租户。'
              : '此 Skill 版本只读。'
      } />}
      {forkMutation.isError && <Alert type="error" showIcon message="创建 Skill 草稿失败" description={(forkMutation.error as Error).message} />}
      {publishMutation.isError && <Alert type="error" showIcon message="发布 Skill 失败" description={(publishMutation.error as Error).message} />}
      {previewMutation.isError && <Alert type="error" message={(previewMutation.error as Error | null)?.message ?? 'ZIP 预览失败'} />}
      {previewMutation.isPending && <Alert type="info" message="正在校验 ZIP 并生成文件树预览…" />}

      <div className="skill-file-workspace-grid">
        <Card className="management-card skill-file-tree-card" title="文件树" extra={<Text type="secondary">{files.length} 个文件</Text>}>
          {files.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="此版本没有可浏览的文件" />
          ) : (
            <Tree
              blockNode
              showIcon={false}
              defaultExpandAll
              treeData={nodes}
              selectedKeys={selectedPath ? [selectedPath] : []}
              onSelect={(_, info) => {
                const selected = (info.node as FileTreeNode).filePath
                if (selected) onSelectFile(selected)
              }}
            />
          )}
        </Card>
        <Card className="management-card skill-file-editor-card" title="文件内容">
          {renderFileDetails()}
        </Card>
      </div>

      <Modal
        title="新建 Skill 文件"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createForm.submit()}
        okText="创建文件"
        cancelText="取消"
        confirmLoading={createMutation.isPending}
        destroyOnHidden
      >
        <Form
          form={createForm}
          layout="vertical"
          onFinish={(values) => createMutation.mutate({ skillId, version, input: values, fakeUser })}
        >
          <Form.Item name="file_path" label="文件路径" rules={[{ required: true, message: '请输入相对路径' }]}>
            <Input placeholder="SKILL.md 或 references/guide.md" />
          </Form.Item>
          <Form.Item name="content_text" label="文件正文" rules={[{ required: true, message: '请输入文本正文' }]}>
            <Input.TextArea rows={8} spellCheck={false} />
          </Form.Item>
          {createMutation.isError && <Alert type="error" message={(createMutation.error as Error).message} />}
        </Form>
      </Modal>

      <Modal
        title="重命名 Skill 文件"
        open={renameOpen}
        onCancel={() => setRenameOpen(false)}
        onOk={() => renameForm.submit()}
        okText="保存路径"
        cancelText="取消"
        confirmLoading={renameMutation.isPending}
        destroyOnHidden
      >
        <Form form={renameForm} layout="vertical" onFinish={(values) => renameMutation.mutate(values.new_file_path)}>
          <Form.Item name="new_file_path" label="新路径" rules={[{ required: true, message: '请输入新路径' }]}>
            <Input />
          </Form.Item>
          {renameMutation.isError && <Alert type="error" message={(renameMutation.error as Error).message} />}
        </Form>
      </Modal>

      <Modal
        title="ZIP 导入预览"
        open={Boolean(zipPreview)}
        onCancel={() => { setZipPreview(undefined); setZipFile(undefined) }}
        onOk={() => commitMutation.mutate()}
        okText="确认导入"
        cancelText="取消"
        confirmLoading={commitMutation.isPending}
        width={720}
        destroyOnHidden
      >
        {zipPreview && (
          <Space direction="vertical" size={16} className="zip-preview-content">
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="目标入口">{zipPreview.entry_path}</Descriptions.Item>
              <Descriptions.Item label="文件数">{zipPreview.file_count}</Descriptions.Item>
              <Descriptions.Item label="ZIP SHA-256">{zipPreview.archive_sha256}</Descriptions.Item>
              <Descriptions.Item label="清单 SHA-256">{zipPreview.manifest_sha256}</Descriptions.Item>
            </Descriptions>
            <Space>
              <Text>导入策略</Text>
              <Radio.Group value={zipStrategy} onChange={(event) => {
                const strategy = event.target.value as 'replace' | 'merge'
                setZipStrategy(strategy)
                if (zipFile) previewMutation.mutate(zipFile)
              }}>
                <Radio.Button value="replace">完整替换</Radio.Button>
                <Radio.Button value="merge">合并</Radio.Button>
              </Radio.Group>
            </Space>
            <List
              size="small"
              header="上传文件"
              dataSource={zipPreview.files}
              renderItem={(item) => <List.Item>{item.path} <Text type="secondary">{item.size_bytes} bytes · {item.sha256.slice(0, 10)}</Text></List.Item>}
            />
            <Space wrap>
              {zipPreview.diff.added.map((path) => <Tag color="green" key={`add:${path}`}>新增 {path}</Tag>)}
              {zipPreview.diff.updated.map((path) => <Tag color="orange" key={`update:${path}`}>更新 {path}</Tag>)}
              {zipPreview.diff.removed.map((path) => <Tag color="red" key={`remove:${path}`}>移除 {path}</Tag>)}
            </Space>
            {commitMutation.isError && <Alert type="error" message={(commitMutation.error as Error).message} />}
          </Space>
        )}
      </Modal>
    </section>
  )
}
