import time
from django.test import TestCase, RequestFactory
from django.core.cache import cache
from django.http import HttpResponse
from core.tasks import dispatch_async_task
from core.ratelimit import ratelimit, get_client_ip


class CoreInfrastructureTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def test_dispatch_async_task_executes_successfully(self):
        """Verifies that dispatch_async_task runs work in the bounded thread pool."""
        result_box = []

        def worker_func(x, y):
            time.sleep(0.01)
            result_box.append(x + y)
            return x + y

        future = dispatch_async_task(worker_func, 10, 25)
        # Wait for the future to finish
        result = future.result(timeout=2)
        self.assertEqual(result, 35)
        self.assertEqual(result_box, [35])

    def test_dispatch_async_task_handles_exceptions_safely(self):
        """Verifies that an exception in background worker does not crash the server."""
        def faulty_func():
            raise ValueError("Erreur simulée dans tâche de fond")

        future = dispatch_async_task(faulty_func)
        result = future.result(timeout=2)
        self.assertIsNone(result)

    def test_rate_limiter_allows_under_limit(self):
        """Requests under threshold succeed."""
        @ratelimit(max_requests=3, window_seconds=60)
        def sample_view(request):
            return HttpResponse("OK", status=200)

        request = self.factory.get('/test/')
        for _ in range(3):
            resp = sample_view(request)
            self.assertEqual(resp.status_code, 200)

    def test_rate_limiter_blocks_above_limit(self):
        """Requests exceeding threshold return HTTP 429."""
        @ratelimit(max_requests=2, window_seconds=60)
        def protected_view(request):
            return HttpResponse("OK", status=200)

        request = self.factory.get('/protected/')
        # 1st request -> 200
        r1 = protected_view(request)
        self.assertEqual(r1.status_code, 200)
        # 2nd request -> 200
        r2 = protected_view(request)
        self.assertEqual(r2.status_code, 200)
        # 3rd request -> 429
        r3 = protected_view(request)
        self.assertEqual(r3.status_code, 429)

    def test_client_ip_extraction(self):
        """Extracts remote IP or X-Forwarded-For."""
        req1 = self.factory.get('/test/', HTTP_X_FORWARDED_FOR='197.234.219.12, 10.0.0.1')
        self.assertEqual(get_client_ip(req1), '197.234.219.12')

        req2 = self.factory.get('/test/', REMOTE_ADDR='41.202.192.5')
        self.assertEqual(get_client_ip(req2), '41.202.192.5')
