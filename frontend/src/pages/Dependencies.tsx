import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  message,
} from 'antd';
import { ArrowRightOutlined, DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import * as api from '../api/endpoints';
import { apiErrorMessage } from '../store/appStore';
import { useIntl } from '../i18n';
import PageHeader from '../components/PageHeader';
import type { DependencyEdge } from '../api/types';

const EDGE_TYPES = ['build', 'runtime', 'data', 'release', 'other'];

export default function DependenciesPage() {
  const intl = useIntl();
  const [edges, setEdges] = useState<DependencyEdge[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [impactProject, setImpactProject] = useState<string | undefined>(undefined);
  const [impact, setImpact] = useState<string[]>([]);
  const [impactLoading, setImpactLoading] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { edges: list } = await api.fetchDependencies();
      setEdges(list);
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!impactProject) {
      setImpact([]);
      return;
    }
    let cancelled = false;
    setImpactLoading(true);
    api
      .fetchDependencyImpact(impactProject)
      .then(({ impact: list }) => {
        if (!cancelled) setImpact(list);
      })
      .catch((err) => {
        if (!cancelled) message.error(apiErrorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setImpactLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [impactProject]);

  const projectNodes = useMemo(() => {
    const nodes = new Set<string>();
    for (const edge of edges) {
      nodes.add(edge.from_project);
      nodes.add(edge.to_project);
    }
    return [...nodes].sort();
  }, [edges]);

  const handleCreate = useCallback(async () => {
    let values: {
      from_project?: string;
      to_project?: string;
      from_task?: string;
      to_task?: string;
      type?: string;
    };
    try {
      values = await form.validateFields();
    } catch {
      return; // 校验失败：antd 已在表单项内提示
    }
    setCreating(true);
    try {
      await api.createDependency({
        from_project: String(values.from_project ?? ''),
        to_project: String(values.to_project ?? ''),
        from_task: String(values.from_task ?? ''),
        to_task: String(values.to_task ?? ''),
        type: String(values.type ?? ''),
      });
      message.success(intl.formatMessage({ id: 'dependencies.created', defaultMessage: 'Dependency edge created' }));
      setModalOpen(false);
      form.resetFields();
      void load();
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setCreating(false);
    }
  }, [form, load, intl]);

  const handleDelete = useCallback(
    async (edgeId: string) => {
      try {
        await api.deleteDependency(edgeId);
        message.success(intl.formatMessage({ id: 'dependencies.deleted', defaultMessage: 'Dependency edge removed' }));
        void load();
      } catch (err) {
        message.error(apiErrorMessage(err));
      }
    },
    [load, intl],
  );

  const columns = useMemo(
    () => [
      {
        title: intl.formatMessage({ id: 'dependencies.col.from', defaultMessage: 'From project' }),
        dataIndex: 'from_project',
        render: (value: string) => <Tag color="blue">{value}</Tag>,
      },
      {
        title: intl.formatMessage({ id: 'dependencies.col.fromTask', defaultMessage: 'From task' }),
        dataIndex: 'from_task',
        render: (value?: string) => value || '-',
      },
      {
        title: '',
        key: 'arrow',
        width: 40,
        render: () => <ArrowRightOutlined style={{ color: '#999' }} />,
      },
      {
        title: intl.formatMessage({ id: 'dependencies.col.to', defaultMessage: 'To project' }),
        dataIndex: 'to_project',
        render: (value: string) => <Tag color="green">{value}</Tag>,
      },
      {
        title: intl.formatMessage({ id: 'dependencies.col.toTask', defaultMessage: 'To task' }),
        dataIndex: 'to_task',
        render: (value?: string) => value || '-',
      },
      {
        title: intl.formatMessage({ id: 'dependencies.col.type', defaultMessage: 'Type' }),
        dataIndex: 'type',
        render: (value?: string) => (value ? <Tag>{value}</Tag> : '-'),
      },
      {
        title: intl.formatMessage({ id: 'dependencies.col.created', defaultMessage: 'Created at' }),
        dataIndex: 'created_at',
        render: (value?: string) => value || '-',
      },
      {
        title: '',
        key: 'actions',
        render: (_: unknown, record: DependencyEdge) => (
          <Popconfirm
            title={intl.formatMessage({
              id: 'dependencies.deleteConfirm',
              defaultMessage: 'Remove this dependency edge?',
            })}
            onConfirm={() => handleDelete(record.id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} data-testid={`delete-dependency-${record.id}`} />
          </Popconfirm>
        ),
      },
    ],
    [intl, handleDelete],
  );

  return (
    <div data-testid="dependencies-page">
      <PageHeader
        title={intl.formatMessage({ id: 'dependencies.header.title', defaultMessage: 'Dependency graph' })}
        description={intl.formatMessage({
          id: 'dependencies.header.desc',
          defaultMessage: 'Cross-project dependencies: edges, cycle detection and impact analysis',
        })}
        actions={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
              {intl.formatMessage({ id: 'common.refresh', defaultMessage: 'Refresh' })}
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)} data-testid="add-dependency-btn">
              {intl.formatMessage({ id: 'dependencies.add', defaultMessage: 'Add dependency' })}
            </Button>
          </Space>
        }
      />
      <Card
        loading={loading}
        title={intl.formatMessage({ id: 'dependencies.graph.title', defaultMessage: 'Graph overview' })}
        data-testid="dependency-graph-card"
      >
        {edges.length === 0 ? (
          <Empty
            description={intl.formatMessage({
              id: 'dependencies.graph.empty',
              defaultMessage: 'No dependency edges yet; add one to see the graph',
            })}
            data-testid="dependency-graph-empty"
          />
        ) : (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Space wrap size={[8, 8]} data-testid="dependency-nodes">
              {projectNodes.map((node) => (
                <Tag key={node} color="geekblue" data-testid={`dependency-node-${node}`}>
                  {node}
                </Tag>
              ))}
            </Space>
            <Space direction="vertical" size={4} data-testid="dependency-links">
              {edges.map((edge) => (
                <span key={edge.id} data-testid={`dependency-link-${edge.id}`}>
                  <Tag color="blue">{edge.from_project}</Tag>
                  <ArrowRightOutlined style={{ marginInline: 4 }} />
                  <Tag color="green">{edge.to_project}</Tag>
                  {edge.type ? <Tag style={{ marginLeft: 4 }}>{edge.type}</Tag> : null}
                </span>
              ))}
            </Space>
          </Space>
        )}
      </Card>
      <Card
        style={{ marginTop: 16 }}
        title={intl.formatMessage({ id: 'dependencies.impact.title', defaultMessage: 'Impact analysis' })}
        data-testid="impact-panel"
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Select
            data-testid="impact-project-select"
            allowClear
            placeholder={intl.formatMessage({
              id: 'dependencies.impact.select',
              defaultMessage: 'Select a project',
            })}
            style={{ width: 280 }}
            loading={impactLoading}
            value={impactProject}
            onChange={(value?: string) => setImpactProject(value || undefined)}
            options={projectNodes.map((node) => ({ value: node, label: node }))}
            virtual={false}
          />
          {impactProject && (
            <div data-testid="impact-result">
              <Space size={8} wrap>
                {impact.length === 0 ? (
                  <span data-testid="impact-empty">
                    {intl.formatMessage({
                      id: 'dependencies.impact.empty',
                      defaultMessage: 'No downstream projects affected',
                    })}
                  </span>
                ) : (
                  <>
                    <span>
                      {intl.formatMessage(
                        { id: 'dependencies.impact.for', defaultMessage: 'Downstream of {project}' },
                        { project: impactProject },
                      )}
                    </span>
                    {impact.map((project) => (
                      <Tag key={project} color="orange" data-testid={`impact-tag-${project}`}>
                        {project}
                      </Tag>
                    ))}
                  </>
                )}
              </Space>
            </div>
          )}
        </Space>
      </Card>
      <Card
        style={{ marginTop: 16 }}
        title={intl.formatMessage({ id: 'dependencies.table.title', defaultMessage: 'Dependency edges' })}
      >
        <Table
          rowKey="id"
          data-testid="dependency-table"
          dataSource={edges}
          columns={columns}
          pagination={false}
          size="small"
        />
      </Card>
      <Modal
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false);
          form.resetFields();
        }}
        onOk={() => void handleCreate()}
        confirmLoading={creating}
        title={intl.formatMessage({ id: 'dependencies.modal.title', defaultMessage: 'New dependency edge' })}
        data-testid="create-dependency-modal"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="from_project"
            label={intl.formatMessage({
              id: 'dependencies.modal.from',
              defaultMessage: 'From project (depends on)',
            })}
            rules={[
              {
                required: true,
                message: intl.formatMessage({
                  id: 'dependencies.modal.fromRequired',
                  defaultMessage: 'Please enter the from project',
                }),
              },
            ]}
          >
            <Input data-testid="dependency-from-input" placeholder="payments" />
          </Form.Item>
          <Form.Item
            name="to_project"
            label={intl.formatMessage({
              id: 'dependencies.modal.to',
              defaultMessage: 'To project (dependency)',
            })}
            rules={[
              {
                required: true,
                message: intl.formatMessage({
                  id: 'dependencies.modal.toRequired',
                  defaultMessage: 'Please enter the to project',
                }),
              },
            ]}
          >
            <Input data-testid="dependency-to-input" placeholder="ledger" />
          </Form.Item>
          <Space size="middle" style={{ display: 'flex' }}>
            <Form.Item
              name="from_task"
              label={intl.formatMessage({ id: 'dependencies.modal.fromTask', defaultMessage: 'From task' })}
              style={{ flex: 1 }}
            >
              <Input data-testid="dependency-from-task-input" />
            </Form.Item>
            <Form.Item
              name="to_task"
              label={intl.formatMessage({ id: 'dependencies.modal.toTask', defaultMessage: 'To task' })}
              style={{ flex: 1 }}
            >
              <Input data-testid="dependency-to-task-input" />
            </Form.Item>
          </Space>
          <Form.Item
            name="type"
            label={intl.formatMessage({ id: 'dependencies.modal.type', defaultMessage: 'Type' })}
          >
            <Select
              data-testid="dependency-type-select"
              allowClear
              placeholder="-"
              options={EDGE_TYPES.map((value) => ({ value, label: value }))}
              virtual={false}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
