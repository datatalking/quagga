import quagga
import theano
import numpy as np
from itertools import izip
from unittest import TestCase
from theano import tensor as T
from quagga.matrix import Matrix
from quagga.context import Context
from quagga.blocks import DotBlock
from quagga.connector import Connector
from quagga.blocks import SigmoidCeBlock
from quagga.blocks import NonlinearityBlock


class TestNonlinearityBlock(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rng = np.random.RandomState(seed=42)
        cls.N = 10

    def test_fprop(self):
        """
        compare `fprop` results for cpu and gpu backends
        """
        r = []
        for i in xrange(self.N):
            batch_size, x_dim = self.rng.random_integers(3000, size=2)
            x = self.rng.rand(batch_size, x_dim).astype(dtype=np.float32)

            for nonlinearity in ['sigmoid', 'tanh', 'relu']:
                state = self.rng.get_state()
                quagga.processor_type = 'gpu'
                x_gpu = Connector(Matrix.from_npa(x))
                nonlinearity_block = NonlinearityBlock(x_gpu, nonlinearity)
                nonlinearity_block.fprop()
                output_gpu = nonlinearity_block.output.to_host()

                self.rng.set_state(state)
                quagga.processor_type = 'cpu'
                x_cpu = Connector(Matrix.from_npa(x))
                nonlinearity_block = NonlinearityBlock(x_cpu, nonlinearity)
                nonlinearity_block.fprop()
                output_cpu = nonlinearity_block.output.to_host()

                r.append(np.allclose(output_gpu, output_cpu))

        self.assertEqual(sum(r), self.N * 3)

    def test_bprop(self):
        """
        compare `bprop` results for cpu and gpu backends
        """
        r = []
        for i in xrange(self.N):
            batch_size, x_dim = self.rng.random_integers(3000, size=2)
            x = self.rng.rand(batch_size, x_dim).astype(dtype=np.float32)

            for nonlinearity in ['sigmoid', 'tanh', 'relu']:
                state = self.rng.get_state()
                quagga.processor_type = 'gpu'
                context = Context()
                x_gpu = Connector(Matrix.from_npa(x), context, context)
                nonlinearity_block = NonlinearityBlock(x_gpu, nonlinearity)
                nonlinearity_block.fprop()
                _, dL_doutput = nonlinearity_block.output.register_usage(context, context)
                random_matrix = self.rng.rand(dL_doutput.nrows, dL_doutput.ncols)
                Matrix.from_npa(random_matrix, 'float').copy(context, dL_doutput)
                nonlinearity_block.bprop()
                dL_dx_gpu = x_gpu.backward_matrix.to_host()

                self.rng.set_state(state)
                quagga.processor_type = 'cpu'
                context = Context()
                x_cpu = Connector(Matrix.from_npa(x), context, context)
                nonlinearity_block = NonlinearityBlock(x_cpu, nonlinearity)
                nonlinearity_block.fprop()
                _, dL_doutput = nonlinearity_block.output.register_usage(context, context)
                random_matrix = self.rng.rand(dL_doutput.nrows, dL_doutput.ncols)
                Matrix.from_npa(random_matrix, 'float').copy(context, dL_doutput)
                nonlinearity_block.bprop()
                dL_dx_cpu = x_cpu.backward_matrix.to_host()

                r.append(np.allclose(dL_dx_gpu, dL_dx_cpu))

        self.assertEqual(sum(r), self.N * 3)

    def test_theano_grad(self):
        class LogisticRegressionLayer(object):
            def __init__(self, W_init, b_init):
                self.W = theano.shared(value=W_init())
                self.b = theano.shared(value=b_init()[0])

            def get_output_expr(self, input_expr):
                return T.nnet.sigmoid(T.dot(input_expr, self.W) + self.b)

        quagga.processor_type = 'gpu'
        r = []
        for i in xrange(self.N):
            batch_size, x_dim = self.rng.random_integers(3000, size=2)
            x = self.rng.rand(batch_size, x_dim).astype(dtype=np.float32)
            lrdot_W_init = lambda: self.rng.rand(x_dim, 1).astype(dtype=np.float32)
            lrdot_b_init = lambda: self.rng.rand(1, 1).astype(dtype=np.float32)
            true_labels = self.rng.randint(2, size=(batch_size, 1)).astype(dtype=np.float32)

            for nonlinearity in ['sigmoid', 'tanh', 'relu']:
                # Theano model
                state = self.rng.get_state()
                th_x = T.fmatrix()
                th_true_labels = T.fmatrix()
                lr_layer = LogisticRegressionLayer(lrdot_W_init, lrdot_b_init)
                if nonlinearity == 'sigmoid':
                    f = T.nnet.sigmoid
                elif nonlinearity == 'tanh':
                    f = T.tanh
                elif nonlinearity == 'relu':
                    f = T.nnet.relu
                probs = lr_layer.get_output_expr(f(th_x))
                loss = T.mean(T.nnet.binary_crossentropy(probs, th_true_labels))
                th_grads = T.grad(loss, wrt=[lr_layer.W, lr_layer.b, th_x])
                get_theano_grads = theano.function([th_x, th_true_labels], th_grads)
                th_grads = get_theano_grads(x, true_labels)

                # quagga model
                self.rng.set_state(state)
                context = Context()
                x_gpu = Connector(Matrix.from_npa(x), context, context)
                true_labels_gpu = Connector(Matrix.from_npa(true_labels))
                nonlinearity_block = NonlinearityBlock(x_gpu, nonlinearity)
                lrdot_block = DotBlock(lrdot_W_init, lrdot_b_init, nonlinearity_block.output)
                sce_block = SigmoidCeBlock(lrdot_block.output, true_labels_gpu)
                nonlinearity_block.fprop()
                lrdot_block.fprop()
                sce_block.fprop()
                sce_block.bprop()
                lrdot_block.bprop()
                nonlinearity_block.bprop()
                q_grads = [lrdot_block.dL_dW.to_host(),
                           lrdot_block.dL_db.to_host(),
                           x_gpu.backward_matrix.to_host()]

                for q_grad, th_grad in izip(q_grads, th_grads):
                    r.append(np.allclose(q_grad, th_grad))

        self.assertEqual(sum(r), self.N * 9)