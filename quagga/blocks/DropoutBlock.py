from quagga.matrix import Matrix
from quagga.context import Context
from quagga.connector import Connector


class DropoutBlock(object):
    def __init__(self, x, dropout_prob, seed=42, device_id=None):
        self.dropout_prob = dropout_prob
        self.context = Context(device_id)
        device_id = self.context.device_id
        self.generator = Matrix.get_random_generator(seed)
        if x.bpropagable:
            self.x, self.dL_dx = x.register_usage(device_id, device_id)
        else:
            self.x = x.register_usage(device_id)
        self.output = Matrix.empty_like(self.x)
        self.output = Connector(self.output, device_id if x.bpropagable else None)
        self.training_mode = True

    def fprop(self):
        if self.training_mode:
            self.x.dropout(self.context, self.generator, self.dropout_prob, self.output)
        else:
            self.x.scale(self.context, 1.0 - self.dropout_prob, self.output)
        self.output.fprop()

    def bprop(self):
        if hasattr(self, 'dL_dx') and self.training_mode:
            dL_doutput = self.output.backward_matrix
            self.dL_dx.add_mask_zeros(self.context, dL_doutput, self.output)

    def set_training_mode(self):
        self.training_mode = True

    def set_testing_mode(self):
        self.training_mode = False