import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Card, Form, Input, InputNumber, Modal, Popconfirm, Space, Table, message } from 'antd';
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import * as api from '../api/endpoints';
import { apiErrorMessage } from '../store/appStore';
import { useIntl } from '../i18n';
import PageHeader from '../components/PageHeader';
import type { Tenant, TenantUsage } from '../api/types';

export default function Tenants() {
  const intl = useIntl();
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [usage, setUsage] = useState<Record<string, TenantUsage>>({});
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { tenants: tenantData } = await api.fetchTenants();
      setTenants(tenantData);
      const usageMap: Record<string, TenantUsage> = {};
      await Promise.all(
        tenantData.map(async (tenant) => {
          try {
            const { usage: item } = await api.fetchTenantUsage(tenant.id);
            usageMap[tenant.id] = item;
          } catch {
            usageMap[tenant.id] = {
              projects: 0,
              sessions: 0,
              project_limit: tenant.project_limit,
              session_limit: tenant.session_limit,
            };
          }
        }),
      );
      setUsage(usageMap);
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = useCallback(async () => {
    const values = await form.validateFields();
    setCreating(true);
    try {
      await api.createTenant({
        id: values.id.trim(),
        name: values.name.trim(),
        project_limit: Number(values.project_limit ?? 0),
        session_limit: Number(values.session_limit ?? 0),
      });
      message.success(intl.formatMessage({ id: 'tenants.created', defaultMessage: 'Tenant created' }));
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
    async (tenantId: string) => {
      try {
        await api.deleteTenant(tenantId);
        message.success(intl.formatMessage({ id: 'tenants.deleted', defaultMessage: 'Tenant removed' }));
        void load();
      } catch (err) {
        message.error(apiErrorMessage(err));
      }
    },
    [load, intl],
  );

  const quotaText = useCallback(
    (used: number | undefined, limit: number | undefined) => `${used ?? 0}/${limit && limit > 0 ? limit : '∞'}`,
    [],
  );

  const columns = useMemo(
    () => [
      { title: intl.formatMessage({ id: 'tenants.col.id', defaultMessage: 'ID' }), dataIndex: 'id', render: (v: string) => <span className="mono">{v}</span> },
      { title: intl.formatMessage({ id: 'tenants.col.name', defaultMessage: 'Name' }), dataIndex: 'name' },
      {
        title: intl.formatMessage({ id: 'tenants.col.projects', defaultMessage: 'Projects' }),
        dataIndex: 'id',
        render: (_: string, record: Tenant) => quotaText(usage[record.id]?.projects, usage[record.id]?.project_limit),
      },
      {
        title: intl.formatMessage({ id: 'tenants.col.sessions', defaultMessage: 'Sessions' }),
        dataIndex: 'id',
        render: (_: string, record: Tenant) => quotaText(usage[record.id]?.sessions, usage[record.id]?.session_limit),
      },
      {
        title: intl.formatMessage({ id: 'tenants.col.created', defaultMessage: 'Created' }),
        dataIndex: 'created_at',
        render: (v?: string) => (v ? new Date(v).toLocaleString() : '-'),
      },
      {
        title: '',
        key: 'actions',
        render: (_: unknown, record: Tenant) => (
          <Popconfirm
            title={intl.formatMessage({ id: 'tenants.deleteConfirm', defaultMessage: 'Remove this tenant?' })}
            onConfirm={() => handleDelete(record.id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} data-testid={`delete-tenant-${record.id}`} />
          </Popconfirm>
        ),
      },
    ],
    [intl, usage, quotaText, handleDelete],
  );

  return (
    <div data-testid="tenants-page">
      <PageHeader
        title={intl.formatMessage({ id: 'tenants.header.title', defaultMessage: 'Tenants' })}
        description={intl.formatMessage({ id: 'tenants.header.desc', defaultMessage: 'Multi-tenant isolation: namespaced storage, quotas and per-tenant config' })}
        actions={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void load()} data-testid="refresh-tenants-btn" />
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)} data-testid="add-tenant-btn">
              {intl.formatMessage({ id: 'tenants.create', defaultMessage: 'New tenant' })}
            </Button>
          </Space>
        }
      />
      <Card>
        <Table<Tenant>
          rowKey="id"
          columns={columns}
          dataSource={tenants}
          loading={loading}
          pagination={false}
          locale={{ emptyText: intl.formatMessage({ id: 'tenants.empty', defaultMessage: 'No tenants' }) }}
          data-testid="tenants-table"
        />
      </Card>
      <Modal
        title={intl.formatMessage({ id: 'tenants.modal.title', defaultMessage: 'New tenant' })}
        open={modalOpen}
        onOk={handleCreate}
        confirmLoading={creating}
        onCancel={() => setModalOpen(false)}
        okText={intl.formatMessage({ id: 'common.create', defaultMessage: 'Create' })}
        cancelText={intl.formatMessage({ id: 'common.cancel', defaultMessage: 'Cancel' })}
        destroyOnHidden
        data-testid="create-tenant-modal"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="id"
            label={intl.formatMessage({ id: 'tenants.modal.id', defaultMessage: 'Tenant ID' })}
            rules={[{ required: true, message: intl.formatMessage({ id: 'tenants.modal.idRequired', defaultMessage: 'Please enter the tenant ID' }) }]}
          >
            <Input data-testid="tenant-id-input" />
          </Form.Item>
          <Form.Item
            name="name"
            label={intl.formatMessage({ id: 'tenants.modal.name', defaultMessage: 'Tenant name' })}
            rules={[{ required: true, message: intl.formatMessage({ id: 'tenants.modal.nameRequired', defaultMessage: 'Please enter the tenant name' }) }]}
          >
            <Input data-testid="tenant-name-input" />
          </Form.Item>
          <Space size="large" style={{ display: 'flex' }}>
            <Form.Item
              name="project_limit"
              label={intl.formatMessage({ id: 'tenants.modal.projectLimit', defaultMessage: 'Project limit (0 = unlimited)' })}
              initialValue={0}
            >
              <InputNumber min={0} data-testid="tenant-project-limit-input" />
            </Form.Item>
            <Form.Item
              name="session_limit"
              label={intl.formatMessage({ id: 'tenants.modal.sessionLimit', defaultMessage: 'Session limit (0 = unlimited)' })}
              initialValue={0}
            >
              <InputNumber min={0} data-testid="tenant-session-limit-input" />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  );
}
