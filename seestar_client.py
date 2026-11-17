"""Seestar communication, called from the application's worker thread."""
from ipaddress import IPv4Address


class SeestarClient:
    def __init__(self, host):
        self.host = str(IPv4Address(host.strip()))

    @staticmethod
    def discover():
        from seestarpy import connection
        connection.discover(quiet=True)
        return list(dict.fromkeys(connection.AVAILABLE_IPS.values()))

    @classmethod
    def discover_devices(cls):
        devices = []
        for host in cls.discover():
            try:
                state = cls(host).connect()
                device = state.get('device') or {}
                model = (device.get('product_model') or device.get('model')
                         or state.get('product_model') or state.get('model'))
            except Exception:
                model = None
            devices.append({'host': host, 'model': str(model) if model else '機種名不明'})
        return devices

    @staticmethod
    def check_response(response):
        if not isinstance(response, dict):
            raise ConnectionError('Seestarから有効な応答がありません。')
        if response.get('error') or response.get('code', 0) != 0:
            raise RuntimeError(f'Seestarが操作を拒否しました: {response}')
        if 'result' not in response:
            raise ConnectionError('Seestarの応答に処理結果がありません。')
        return response['result']

    def _call(self, function, *args):
        # Route each call explicitly; never rely on auto-discovery's default IP.
        from seestarpy import connection
        # seestarpy 0.8 only accepts addresses present in AVAILABLE_IPS,
        # including manually entered, valid IPv4 addresses.
        connection.AVAILABLE_IPS.setdefault(self.host, self.host)
        response = connection.multiple_ips(function)(*args, ips=self.host)
        return self.check_response(response)

    def connect(self):
        from seestarpy.connection import send_command
        result = self._call(send_command, {'method': 'get_device_state'})
        if not isinstance(result, dict) or not result:
            raise ConnectionError('Seestarの機器情報を取得できませんでした。')
        return result

    def transfer(self, plan):
        """Send and execute a plan; the caller must explain this to the user."""
        from seestarpy.plan import set_view_plan
        return self._call(set_view_plan, plan)
