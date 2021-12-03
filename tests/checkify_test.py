# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import unittest

from absl.testing import absltest
from absl.testing import parameterized

import jax
import jax.numpy as jnp
from jax import lax
from jax.config import config
from jax.experimental import checkify
import jax._src.test_util as jtu

config.parse_flags_with_absl()


class CheckifyTransformTests(jtu.JaxTestCase):
  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_jit={}".format(jit), "jit": jit}
      for jit in [False, True]))
  @jtu.skip_on_devices('tpu')
  def test_jit_nan(self, jit):
    def f(x1, x2):
      y1 = jnp.sin(x1)
      y2 = jnp.sin(x2)
      return y1 + y2

    f = jax.jit(f) if jit else f

    err, _ = checkify.checkify(f)(3., 4.)
    self.assertIs(err.get(), None)

    err, _ = checkify.checkify(f)(3., jnp.inf)
    self.assertIsNotNone(err.get())
    self.assertStartsWith(err.get(), 'nan generated by primitive sin')

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_jit={}".format(jit), "jit": jit}
      for jit in [False, True]))
  def test_jit_oob(self, jit):
    def f(x, i):
      y = jnp.sin(x)
      z = y[i]
      w = jnp.cos(z)
      return w

    f = jax.jit(f) if jit else f

    err, _ = checkify.checkify(f)(jnp.arange(3), 2)
    self.assertIs(err.get(), None)

    err, _ = checkify.checkify(f)(jnp.arange(3), 5)
    self.assertIsNotNone(err.get())
    self.assertStartsWith(err.get(), 'out-of-bounds indexing')

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_jit={}".format(jit), "jit": jit}
      for jit in [False, True]))
  @jtu.skip_on_devices('tpu')
  def test_jit_multi(self, jit):
    def f(x, i):
      y = x[i]
      z = jnp.cos(y)
      return z

    f = jax.jit(f) if jit else f

    # no error
    err, _ = checkify.checkify(f)(jnp.array([0., jnp.inf, 2.]), 2)
    self.assertIs(err.get(), None)

    # oob error
    err, _ = checkify.checkify(f)(jnp.array([0., 1., 2.]), 5)
    self.assertIsNotNone(err.get())
    self.assertStartsWith(err.get(), 'out-of-bounds indexing')

    # nan error
    err, _ = checkify.checkify(f)(jnp.array([0., 1., jnp.inf]), 2)
    self.assertIsNotNone(err.get())
    self.assertStartsWith(err.get(), 'nan generated by primitive cos')

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_jit={}".format(jit), "jit": jit}
      for jit in [False, True]))
  def test_jit_ordering(self, jit):
    def f(x, i):
      y = x[i]
      z = jnp.sin(x)
      return y * z

    f = jax.jit(f) if jit else f

    # both oob and nan error, but oob happens first
    err, _ = checkify.checkify(f)(jnp.array([0., 1., jnp.inf]), 5)
    self.assertIsNotNone(err.get())
    self.assertStartsWith(err.get(), 'out-of-bounds indexing')

  @jtu.skip_on_devices('tpu')
  def test_pmap_basic(self):
    if len(jax.devices()) < 2:
      raise unittest.SkipTest("requires at least 2 devices")

    @jax.pmap
    def f(x1, x2):
      y1 = jnp.sin(x1)
      y2 = jnp.sin(x2)
      return y1 + y2

    xs = jnp.array([0., 2.])
    err, _ = checkify.checkify(f)(xs, xs)
    self.assertIs(err.get(), None)

    ys = jnp.array([3., jnp.inf])
    err, _ = checkify.checkify(f)(xs, ys)
    self.assertIsNotNone(err.get())
    self.assertStartsWith(err.get(), 'nan generated by primitive sin')

  @jtu.skip_on_devices('tpu')
  def test_cond_basic(self):
    @jax.jit
    def f(x):
      return lax.cond(x > 0,
                      lambda: jnp.sin(x),
                      lambda: x)

    err, y = checkify.checkify(f)(3.)
    self.assertIs(err.get(), None)

    err, y = checkify.checkify(f)(jnp.inf)
    self.assertIsNotNone(err.get())
    self.assertStartsWith(err.get(), 'nan generated by primitive sin')

    err, y = checkify.checkify(f)(-jnp.inf)
    self.assertIs(err.get(), None)


class AssertPrimitiveTests(jtu.JaxTestCase):
  def test_assert_primitive_impl(self):
    def f():
      checkify.assert_(False, "hi")

    with self.assertRaisesRegex(AssertionError, "hi"):
      f()

  def test_assert_primitive_(self):
    @jax.jit
    def f():
      checkify.assert_(False, "hi")

    with self.assertRaisesRegex(Exception, "can't be staged"):
      f()

  def test_assert_discharging(self):
    @checkify.checkify
    def f(x):
      checkify.assert_(x > 0, "must be positive!")
      return jnp.log(x)

    err, y = f(1.)
    self.assertIsNone(err.get())

    err, y = f(0.)
    self.assertIsNotNone(err.get())
    self.assertStartsWith(err.get(), "must be positive")

    f = jax.jit(f)

    err, y = f(1.)
    self.assertIsNone(err.get())

    err, y = f(0.)
    self.assertIsNotNone(err.get())
    self.assertStartsWith(err.get(), "must be positive")

  def test_assert2(self):
    def f(pred):  # note: data dependence needed!
      checkify.assert2_(pred, 0, {0: "hi"})

    with self.assertRaisesRegex(AssertionError, "hi"):
      f(False)

    f = checkify.checkify(f)
    err, none = f(False)

    self.assertIsNone(none)
    self.assertIsNotNone(err.get())
    self.assertStartsWith(err.get(), "hi")

  def test_discharge_recharge(self):
    def ejit(f):
      f = checkify.checkify(f)
      f = jax.jit(f)
      def jitted_f(*args):
        err, out = f(*args)
        checkify.assert2_(~err.err, err.code, err.msgs)
        return out
      return jitted_f

    @ejit
    def f(pred):
      assert python_should_be_running
      checkify.assert_(pred, "foo")

    python_should_be_running = True
    f(True)

    python_should_be_running = False
    f(True)
    with self.assertRaisesRegex(AssertionError, "foo"):
      f(False)


if __name__ == "__main__":
  absltest.main(testLoader=jtu.JaxTestLoader())