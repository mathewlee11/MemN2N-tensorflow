import os
import math
import random
import numpy as np
import tensorflow as tf


class MemN2N(object):
    def __init__(self, sess, nwords=None, edim=150, lindim=75, nhop=6,
                 mem_size=100, batch_size=128, nepoch=100, lr_decay=0.99,
                 init_hid=0.1, init_std=0.05, max_grad_norm=50, init_lr=0.01,
                 data_dir='data', checkpoint_dir='checkpoints',
                 data_name='ptb', is_test=False, show=False):
        if nwords is None:
            raise("Set nwords!")
        self.nwords = nwords
        self.init_hid = init_hid
        self.init_std = init_std
        self.batch_size = batch_size
        self.nepoch = nepoch
        self.nhop = nhop
        self.edim = edim
        self.mem_size = mem_size
        self.lindim = lindim
        self.max_grad_norm = max_grad_norm

        self.show = show
        self.is_test = is_test
        self.checkpoint_dir = checkpoint_dir

        if not os.path.isdir(self.checkpoint_dir):
            raise Exception(" [!] Directory %s not found" % self.checkpoint_dir)

        self.input = tf.placeholder(tf.float32, [None, self.edim], name="input")
        self.time = tf.placeholder(tf.int32, [None, self.mem_size], name="time")
        self.target = tf.placeholder(tf.float32, [self.batch_size, self.nwords], name="target")
        self.context = tf.placeholder(tf.int32, [self.batch_size, self.mem_size], name="context")

        self.hid = []
        self.hid.append(self.input)
        self.share_list = []
        self.share_list.append([])

        self.lr = None
        self.current_lr = init_lr
        self.lr_decay = lr_decay
        self.loss = None
        self.step = None
        self.optim = None

        self.sess = sess
        self.log_loss = []
        self.log_perp = []

    def build_memory(self):
        self.global_step = tf.Variable(0, name="global_step")

        self.A = tf.Variable(tf.random_normal([self.nwords, self.edim], stddev=self.init_std))
        self.B = tf.Variable(tf.random_normal([self.nwords, self.edim], stddev=self.init_std))
        self.C = tf.Variable(tf.random_normal([self.edim, self.edim], stddev=self.init_std))

        # Temporal Encoding
        self.T_A = tf.Variable(tf.random_normal([self.mem_size, self.edim], stddev=self.init_std))
        self.T_B = tf.Variable(tf.random_normal([self.mem_size, self.edim], stddev=self.init_std))

        # m_i = sum A_ij * x_ij + T_A_i
        Ain_c = tf.nn.embedding_lookup(self.A, self.context)
        Ain_t = tf.nn.embedding_lookup(self.T_A, self.time)
        Ain = tf.add(Ain_c, Ain_t)

        # c_i = sum B_ij * u + T_B_i
        Bin_c = tf.nn.embedding_lookup(self.B, self.context)
        Bin_t = tf.nn.embedding_lookup(self.T_B, self.time)
        Bin = tf.add(Bin_c, Bin_t)

        for h in range(self.nhop):
            self.hid3dim = tf.reshape(self.hid[-1], [-1, 1, self.edim])
            Aout = tf.batch_matmul(self.hid3dim, Ain, adj_y=True)
            Aout2dim = tf.reshape(Aout, [-1, self.mem_size])
            P = tf.nn.softmax(Aout2dim)

            probs3dim = tf.reshape(P, [-1, 1, self.mem_size])
            Bout = tf.batch_matmul(probs3dim, Bin)
            Bout2dim = tf.reshape(Bout, [-1, self.edim])

            Cout = tf.matmul(self.hid[-1], self.C)
            Dout = tf.add(Cout, Bout2dim)

            self.share_list[0].append(Cout)

            if self.lindim == self.edim:
                self.hid.append(Dout)
            elif self.lindim == 0:
                self.hid.append(tf.nn.relu(Dout))
            else:
                F = tf.slice(Dout, [0, 0], [self.batch_size, self.lindim])
                G = tf.slice(Dout, [0, self.lindim],
                             [self.batch_size, self.edim-self.lindim])
                K = tf.nn.relu(G)
                self.hid.append(tf.concat(1, [F, K]))

    def build_model(self):
        self.build_memory()

        self.W = tf.Variable(tf.random_normal([self.edim, self.nwords],
                                              stddev=self.init_std))
        self.output = tf.matmul(self.hid[-1], self.W)

        self.loss = tf.nn.softmax_cross_entropy_with_logits(self.output,
                                                            self.target)

        self.lr = tf.Variable(self.current_lr)
        self.opt = tf.train.GradientDescentOptimizer(self.lr)

        params = [self.A, self.B, self.C, self.T_A, self.T_B, self.W]
        grads_and_vars = self.opt.compute_gradients(self.loss, params)
        clipped_grads_and_vars = [(tf.clip_by_norm(gv[0], self.max_grad_norm),
                                   gv[1]) for gv in grads_and_vars]

        inc = self.global_step.assign_add(1)
        with tf.control_dependencies([inc]):
            self.optim = self.opt.apply_gradients(clipped_grads_and_vars)

        tf.initialize_all_variables().run()
        self.saver = tf.train.Saver()

    def train(self, data):
        N = int(math.ceil(len(data) / self.batch_size))
        cost = 0

        x = np.ndarray([self.batch_size, self.edim], dtype=np.float32)
        time = np.ndarray([self.batch_size, self.mem_size], dtype=np.int32)
        target = np.zeros([self.batch_size, self.nwords])  # one-hot-encoded
        context = np.ndarray([self.batch_size, self.mem_size])

        x.fill(self.init_hid)
        for t in range(self.mem_size):
            time[:, t].fill(t)

        if self.show:
            from utils import ProgressBar
            bar = ProgressBar('Train', max=N)

        for idx in range(N):
            if self.show:
                bar.next()
            target.fill(0)
            for b in range(self.batch_size):
                m = random.randrange(self.mem_size, len(data))
                target[b][data[m]] = 1
                context[b] = data[m - self.mem_size:m]

            _, loss, self.step = self.sess.run([self.optim,
                                                self.loss,
                                                self.global_step],
                                                feed_dict={
                                                    self.input: x,
                                                    self.time: time,
                                                    self.target: target,
                                                    self.context: context})
            cost += np.sum(loss)

        if self.show:
            bar.finish()
        return cost/N/self.batch_size

    def test(self, data, label='Test'):
        N = int(math.ceil(len(data) / self.batch_size))
        cost = 0

        x = np.ndarray([self.batch_size, self.edim], dtype=np.float32)
        time = np.ndarray([self.batch_size, self.mem_size], dtype=np.int32)
        target = np.zeros([self.batch_size, self.nwords])  # one-hot-encoded
        context = np.ndarray([self.batch_size, self.mem_size])

        x.fill(self.init_hid)
        for t in range(self.mem_size):
            time[:, t].fill(t)

        if self.show:
            from utils import ProgressBar
            bar = ProgressBar(label, max=N)

        m = self.mem_size
        for idx in range(N):
            if self.show:
                bar.next()
            target.fill(0)
            for b in range(self.batch_size):
                target[b][data[m]] = 1
                context[b] = data[m - self.mem_size:m]
                m += 1

                if m >= len(data):
                    m = self.mem_size

            loss = self.sess.run([self.loss], feed_dict={self.input: x,
                                                         self.time: time,
                                                         self.target: target,
                                                         self.context: context})
            cost += np.sum(loss)

        if self.show:
            bar.finish()
        return cost/N/self.batch_size

    def run(self, train_data, test_data):
        if not self.is_test:
            for idx in range(self.nepoch):
                train_loss = np.sum(self.train(train_data))
                test_loss = np.sum(self.test(test_data, label='Validation'))

                # Logging
                self.log_loss.append([train_loss, test_loss])
                self.log_perp.append([math.exp(train_loss),
                                      math.exp(test_loss)])

                state = {
                    'perplexity': math.exp(train_loss),
                    'epoch': idx,
                    'learning_rate': self.current_lr,
                    'valid_perplexity': math.exp(test_loss)
                }
                print(state)

                # Learning rate annealing
                if len(self.log_loss) > 1 and self.log_loss[idx][1] > self.log_loss[idx-1][1] * 0.9999:
                    self.current_lr = self.current_lr * self.lr_decay
                    self.lr.assign(self.current_lr).eval(session=self.sess)
                if self.current_lr < 1e-5:
                    break

                if idx % 10 == 0:
                    self.saver.save(self.sess,
                                    os.path.join(self.checkpoint_dir, "MemN2N.model"),
                                    global_step=self.step.astype(int))
        else:
            self.load()

            valid_loss = np.sum(self.test(train_data, label='Validation'))
            test_loss = np.sum(self.test(test_data, label='Test'))

            state = {
                'valid_perplexity': math.exp(valid_loss),
                'test_perplexity': math.exp(test_loss)
            }
            print(state)

    def load(self):
        print(" [*] Reading checkpoints...")
        ckpt = tf.train.get_checkpoint_state(self.checkpoint_dir)
        if ckpt and ckpt.model_checkpoint_path:
            self.saver.restore(self.sess, ckpt.model_checkpoint_path)
        else:
            raise Exception(" [!] Trest mode but no checkpoint found")

    def gen_words(self, data, dummy_idx, N):
        # N = int(math.ceil(len(data) / self.batch_size))
        data = data.copy()
        x = np.ndarray([self.batch_size, self.edim], dtype=np.float32)
        time = np.ndarray([self.batch_size, self.mem_size], dtype=np.int32)
        target = np.zeros([self.batch_size, self.nwords])  # one-hot-encoded
        context = np.ndarray([self.batch_size, self.mem_size])

        x.fill(self.init_hid)
        for t in range(self.mem_size):
            time[:, t].fill(t)

        if self.show:
            from utils import ProgressBar
            bar = ProgressBar('Generating', max=N)

        for idx in range(N):
            if self.show:
                bar.next()

        for n in range(N):
            context = np.zeros([self.batch_size, self.mem_size]) + dummy_idx

            min_len = min(len(data), self.mem_size)
            context[:, -min_len:] = data[-min_len:]

            prediction = self.sess.run(self.output, feed_dict={
                    self.input: x,
                    self.time: time,
                    self.target: target,
                    self.context: context})

            predicted_word_index = np.argmax(prediction[0])
            print(predicted_word_index)
            data = np.append(data, predicted_word_index)

        if self.show:
            bar.finish()
        return data
