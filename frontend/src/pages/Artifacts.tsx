import { useCallback, useEffect, useState } from 'react';
import { Breadcrumb, Button, Card, Drawer, Empty, message, Space, Spin, Tree, Typography } from 'antd';
import { FileOutlined, FolderOutlined, ReloadOutlined } from '@ant-design/icons';
import * as api from '../api/endpoints';
import { apiErrorMessage } from '../store/appStore';
import { useProjectParam } from '../hooks/useProjectParam';
import { useIntl } from '../i18n';
import PageHeader from '../components/PageHeader';
import ProjectSelector from '../components/ProjectSelector';
import type { WorkspaceFile, WorkspaceTreeEntry } from '../api/types';

interface TreeNode {
  key: string;
  title: string;
  isLeaf: boolean;
  entry: WorkspaceTreeEntry;
  children?: TreeNode[];
}

export default function Artifacts() {
  const intl = useIntl();
  const [projectId, setProjectId] = useProjectParam();
  const [treeData, setTreeData] = useState<TreeNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [currentPath, setCurrentPath] = useState('');
  const [file, setFile] = useState<WorkspaceFile | null>(null);
  const [fileLoading, setFileLoading] = useState(false);

  const loadDir = useCallback(
    async (path: string): Promise<TreeNode[]> => {
      if (!projectId) return [];
      try {
        const tree = await api.fetchWorkspaceTree(projectId, path);
        setCurrentPath(tree.path);
        return tree.entries.map((entry) => ({
          key: path ? `${path}/${entry.name}` : entry.name,
          title: entry.name,
          isLeaf: entry.type === 'file',
          entry,
          children: entry.type === 'dir' ? [] : undefined,
        }));
      } catch (err) {
        message.error(apiErrorMessage(err));
        return [];
      }
    },
    [projectId],
  );

  const reload = useCallback(async () => {
    setLoading(true);
    const nodes = await loadDir('');
    setTreeData(nodes);
    setLoading(false);
  }, [loadDir]);

  useEffect(() => {
    if (projectId) void reload();
    else setTreeData([]);
  }, [projectId, reload]);

  const onLoadData = useCallback(
    async (node: TreeNode) => {
      const children = await loadDir(node.key);
      setTreeData((prev) => updateTree(prev, node.key, children));
    },
    [loadDir],
  );

  const openFile = useCallback(
    async (path: string) => {
      if (!projectId) return;
      setFileLoading(true);
      try {
        const data = await api.fetchWorkspaceFile(projectId, path);
        setFile(data);
      } catch (err) {
        message.error(apiErrorMessage(err));
      } finally {
        setFileLoading(false);
      }
    },
    [projectId],
  );

  return (
    <div data-testid="artifacts-page">
      <PageHeader
        title={intl.formatMessage({ id: 'artifacts.header.title', defaultMessage: 'Workspace browser' })}
        description={intl.formatMessage({
          id: 'artifacts.header.desc',
          defaultMessage: 'Browse the project workspace file tree and preview file contents',
        })}
      />
      <div style={{ marginBottom: 16 }}>
        <ProjectSelector value={projectId || undefined} onChange={setProjectId} />
      </div>
      {!projectId ? (
        <Empty
          description={intl.formatMessage({ id: 'artifacts.selectProject', defaultMessage: 'Select a project first' })}
        />
      ) : (
        <>
          <Breadcrumb
            style={{ marginBottom: 12 }}
            items={[
              { title: intl.formatMessage({ id: 'artifacts.root', defaultMessage: 'Workspace root' }) },
              ...currentPath
                .split('/')
                .filter(Boolean)
                .map((seg) => ({ title: seg })),
            ]}
            data-testid="workspace-breadcrumb"
          />
          <Card
            title={
              <Space>
                <span>{intl.formatMessage({ id: 'artifacts.fileTree', defaultMessage: 'File tree' })}</span>
                <Button size="small" icon={<ReloadOutlined />} onClick={() => void reload()}>
                  {intl.formatMessage({ id: 'common.refresh', defaultMessage: 'Refresh' })}
                </Button>
              </Space>
            }
          >
            <Spin spinning={loading}>
              {treeData.length === 0 && !loading ? (
                <Empty
                  description={intl.formatMessage({ id: 'artifacts.emptyTree', defaultMessage: 'Workspace is empty' })}
                />
              ) : (
                <Tree<TreeNode>
                  showLine
                  loadData={onLoadData}
                  treeData={treeData}
                  onSelect={(keys) => {
                    const key = keys[0];
                    if (!key) return;
                    const node = findNode(treeData, String(key));
                    if (node?.entry.type === 'file') void openFile(String(key));
                  }}
                  icon={(props) => (props.isLeaf ? <FileOutlined /> : <FolderOutlined />)}
                  aria-label={intl.formatMessage({ id: 'artifacts.treeAria', defaultMessage: 'Workspace file tree' })}
                  data-testid="workspace-tree"
                />
              )}
            </Spin>
          </Card>
        </>
      )}
      <Drawer
        title={file?.path}
        open={!!file}
        onClose={() => setFile(null)}
        width={640}
        loading={fileLoading}
        aria-label={intl.formatMessage({ id: 'artifacts.previewAria', defaultMessage: 'File preview' })}
        data-testid="file-drawer"
      >
        {file && (
          <>
            <Typography.Paragraph type="secondary">
              {file.file.mime} · {file.file.size}{' '}
              {intl.formatMessage({ id: 'artifacts.bytes', defaultMessage: 'bytes' })} · {file.file.name}
            </Typography.Paragraph>
            <pre className="code-preview" data-testid="file-content">
              {file.file.content}
            </pre>
          </>
        )}
      </Drawer>
    </div>
  );
}

function updateTree(nodes: TreeNode[], key: string, children: TreeNode[]): TreeNode[] {
  return nodes.map((node) => {
    if (node.key === key) return { ...node, children };
    if (node.children) return { ...node, children: updateTree(node.children, key, children) };
    return node;
  });
}

function findNode(nodes: TreeNode[], key: string): TreeNode | null {
  for (const node of nodes) {
    if (node.key === key) return node;
    if (node.children) {
      const found = findNode(node.children, key);
      if (found) return found;
    }
  }
  return null;
}
