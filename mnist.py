"""Do sequential mnist"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import datetime
import os
import time

import numpy as np
import tensorflow as tf

import mrnn
import rnndatasets.sequentialmnist as data

import sequential_model as sm

flags = tf.app.flags

flags.DEFINE_integer('width', 100, 'how wide should the recurrent layers be')
flags.DEFINE_integer('layers', 1, 'how many recurrent layers should there be')
flags.DEFINE_bool('project', False, 'If true, adds a projection layer.')
flags.DEFINE_integer('num_epochs', 100, 'how long to train for')
flags.DEFINE_string('results_dir', 'time', 'where to store the results. If `time`'
                                           ' a directory is chosen based on the current time')
flags.DEFINE_float('learning_rate', 0.01, 'learning rate for SGD')
flags.DEFINE_bool('learning_rate_decay', True, 'whether to decay the lr')
flags.DEFINE_integer('batch_size', 100, 'how many examples to use for SGD')
flags.DEFINE_integer('rank', 10, 'the rank of the tensor decompositions')
flags.DEFINE_bool('stabilise_acts', False, 'regularise the successive hidden norms (only works for one layer)')

flags.DEFINE_string('weightnorm', None, 'how to do weight normalisation (if any)')
flags.DEFINE_bool('layernorm', False, 'whether to apply global layer norm (on the states)')
flags.DEFINE_string(
    'cell',
    'lstm',
    'which cell to use. One of: `vanilla`, `cp-relu`, `cp-tanh`, `tt-relu`, '
    '`tt-tanh`, `irnn` or `lstm`.')
flags.DEFINE_string('optimiser', 'momentum', 'whether to use momentum or ADAM')
flags.DEFINE_float('max_grad_norm', 10.0, 'where to clip the global norm of the gradien during backprop')
flags.DEFINE_bool('permute', False, 'If true, a fixed random permutation of the images is used. The'
                                    'see is always `1001`')
flags.DEFINE_integer('starting_step', 0, 'If restarting training from a model '
                     'before we starting saving the global step, where should it be?')

FLAGS = flags.FLAGS


def get_cell(size):
    """Gets an appropriate cell according to the flags.
    At the moment assumes you want input size = size.
    """
    if FLAGS.cell == 'lstm':
        return tf.nn.rnn_cell.BasicLSTMCell(size)  # default forget biases
    if FLAGS.cell == 'vanilla':
        return mrnn.VRNNCell(size, hh_init=mrnn.init.orthonormal_init(0.999))
    if FLAGS.cell == 'vanilla-layernorm':
        return mrnn.VRNNCell(size, hh_init=mrnn.init.orthonormal_init(0.5),
                             weightnorm='layer')
    if FLAGS.cell == 'irnn':
        return mrnn.IRNNCell(size)
    if FLAGS.cell == 'cp-gate':
        return mrnn.CPGateCell(size, FLAGS.rank, candidate_nonlin=tf.nn.relu, separate_pad=True)
    if FLAGS.cell == 'cp-gate-combined':
        return mrnn.CPGateCell(size, FLAGS.rank, candidate_nonlin=tf.nn.relu, separate_pad=False)
    if FLAGS.cell == 'cp-gate-linear':
        return mrnn.CPGateCell(size, FLAGS.rank, candidate_nonlin=tf.identity, separate_pad=True)
    if FLAGS.cell == 'cp-gate-combined-linear':
        return mrnn.CPGateCell(size, FLAGS.rank, candidate_nonlin=tf.identity, separate_pad=False)
    if FLAGS.cell == 'cp-relu':
        return mrnn.SimpleCPCell(size, size, FLAGS.rank,
                                 nonlinearity=tf.nn.relu,
                                 weightnorm=False,
                                 separate_pad=True)
    if FLAGS.cell == 'cp-tanh':
        return mrnn.SimpleCPCell(size, size, FLAGS.rank,
                                 nonlinearity=tf.nn.tanh,
                                 weightnorm=False,
                                 separate_pad=True)
    if FLAGS.cell == 'tt-relu':
        return mrnn.SimpleTTCell(size, size, [FLAGS.rank]*2,
                                 nonlinearity=tf.nn.relu)
    if FLAGS.cell == 'tt-tanh':
        return mrnn.SimpleTTCell(size, size, [FLAGS.rank]*2,
                                 nonlinearity=tf.nn.tanh)
    if FLAGS.cell == 'cp+':
        return mrnn.AdditiveCPCell(size, size, FLAGS.rank, nonlinearity=tf.nn.relu)
    if FLAGS.cell == 'cp+pre':
        return mrnn.AdditiveCPCell(size, size, FLAGS.rank, nonlinearity=tf.nn.relu,
                                   layernorm='pre')
    if FLAGS.cell == 'cp+post':
        return mrnn.AdditiveCPCell(size, size, FLAGS.rank, nonlinearity=tf.nn.relu,
                                   layernorm='post')
    if FLAGS.cell == 'cp+tanh':
        return mrnn.AdditiveCPCell(size, size, FLAGS.rank, nonlinearity=tf.nn.tanh)
    if FLAGS.cell == 'cp+tanhpre':
        return mrnn.AdditiveCPCell(size, size, FLAGS.rank, nonlinearity=tf.nn.tanh,
                                   layernorm='pre')
    if FLAGS.cell == 'cp+tanhpost':
        return mrnn.AdditiveCPCell(size, size, FLAGS.rank, nonlinearity=tf.nn.tanh,
                                   layernorm='post')
    if FLAGS.cell == 'cp+-':
        return mrnn.AddSubCPCell(size, size, FLAGS.rank, nonlinearity=tf.nn.relu)
    if FLAGS.cell == 'cp-del':
        return mrnn.CPDeltaCell(size, size, FLAGS.rank, weightnorm=FLAGS.weightnorm)
    if FLAGS.cell == 'cp-res':
        return mrnn.CPResCell(size, size, FLAGS.rank)
    if FLAGS.cell == 'cp-loss':
        return mrnn.CPLossyIntegrator(size, size, FLAGS.rank)
    if FLAGS.cell == 'cp-int':
        return mrnn.CPSimpleIntegrator(size, size, FLAGS.rank)
    raise ValueError('Unknown cell: {}'.format(FLAGS.cell))


def run_epoch(sess, batch_iter, inputs, targets, train_op, loss, gnorm):
    """Runs an epoch of training (or not training if train_op is tf.no_op()).
    Returns average loss for the epoch"""
    total_loss = 0
    num_steps = 0
    
    for data, labels in batch_iter:
        start = time.time()
        
        feed = {inputs[i]: data[i, ...] for i in range(len(inputs))}
        feed[targets] = labels

        if gnorm is None:
            batch_loss, _ = sess.run([loss, train_op],
                                     feed_dict=feed)
            print('\r batch loss {:.5f}'.format(batch_loss),
                  end='')
        else:
            batch_loss, norm, _ = sess.run([loss, gnorm, train_op],
                                           feed_dict=feed)
            print('\r batch loss {:.5f}  (grad norm: {:.5f})'.format(batch_loss, norm),
                  end='')
        end = time.time()

        print('  ({:.3f} seqs per sec)'.format(data.shape[1]/(end-start)),
              end='')
        
        total_loss += batch_loss
        num_steps += 1
        if np.isnan(batch_loss):
            return batch_loss  # bail early if we've diverged catastrophically
    return total_loss / num_steps


def count_params():
    """does a simple count of how many things are in tf.trainable_variables"""
    total = 0
    for var in tf.trainable_variables():
        prod = 1
        for dim in var.get_shape():
            prod *= dim.value
        total += prod
    return total


def activation_stabiliser(states, global_step, beta=250.0):
    """as per http://arxiv.org/pdf/1511.08400v7.pdf
    (roughly)"""
    beta = tf.train.exponential_decay(beta, global_step, 500, 0.8)
    norms = [tf.sqrt(tf.reduce_sum(tf.square(act), reduction_indices=1))
             for act in states]
    diffs = [b - a for a, b in zip(norms, norms[1:])]
    return beta * tf.reduce_mean(tf.square(tf.pack(diffs)))


def main(_):
    # make a space for results
    if FLAGS.results_dir == 'time':
        results_dir = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    else:
        results_dir = FLAGS.results_dir
    results_file = os.path.join(results_dir, 'results.txt')
    test_results = os.path.join(results_dir, 'test.txt')
    os.makedirs(results_dir, exist_ok=True)

    # now we get the stuff
    # unfortunately we are going to have to do some serious
    # unrolling of the network.
    seq_length = 28*28  # how many mnist pixels
    batch_size = FLAGS.batch_size
    global_step = tf.Variable(FLAGS.starting_step, trainable=False)
    inputs = [tf.placeholder(tf.float32, name='input_{}'.format(i),
                             shape=[batch_size, 1])
              for i in range(seq_length)]
    targets = tf.placeholder(tf.int64, name='targets',
                             shape=[batch_size])
    if FLAGS.learning_rate_decay:
        learning_rate = tf.train.exponential_decay(FLAGS.learning_rate,
                                                   global_step,
                                                   12500, 0.85,
                                                   staircase=True)
    else:
        learning_rate = FLAGS.learning_rate

    print('{:.^40}'.format('getting model'), end='', flush=True)
    with tf.variable_scope('model'):
        cell = get_cell(FLAGS.width)
        if FLAGS.layernorm:
            cell = mrnn.LayerNormWrapper(cell)
        init_state, final_state, logits, outputs = sm.inference(
            inputs, FLAGS.layers, cell, 10)
        loss = sm.loss(logits, targets)
        if FLAGS.stabilise_acts:
            loss += activation_stabiliser(outputs, global_step)  # only works for one layer
        train_op, gnorm = sm.train(loss, learning_rate, global_step, FLAGS.max_grad_norm, optimiser=FLAGS.optimiser)
        accuracy = sm.accuracy(logits, targets)
    print('\r{:\\^40}'.format('got model with {} params'.format(count_params())))
    with open(os.path.join(results_dir, 'params.txt'), 'w') as f:
        f.write('{}'.format(count_params()))

    # set up a saver
    model_dir = os.path.join(results_dir, 'models')
    if os.path.exists(model_dir):
        print('Model directory existings, going to try to load existing')
    else:
        os.mkdir(model_dir)
    model_filename = os.path.join(model_dir, 'model')

    saver = tf.train.Saver(tf.trainable_variables(), max_to_keep=1)

    sess = tf.Session()
    print('{:.^40}'.format('initialising'), end='', flush=True)
    load_filename = tf.train.latest_checkpoint(model_dir)
    if not load_filename:
        sess.run(tf.initialize_all_variables())
        print('\r{:/^40}'.format('initialised fresh'))
    else:
        sess.run(tf.initialize_all_variables())  # there are some that aren't saved
        saver.restore(sess, load_filename)
        print('\r{:/^40}'.format('loaded from {}'.format(load_filename)))

    print('{:.^40}'.format('getting data'), end='', flush=True)
    if FLAGS.permute:
        permutation = data.get_permutation(1001)
    else:
        permutation = None
    _, _, test = data.get_iters(batch_size, permute=permutation)
    print('\r{:\\^40}'.format('got data'))

    for epoch in range(FLAGS.num_epochs):
        train, valid, _ = data.get_iters(batch_size, shuffle=True, permute=permutation)
        # do a training run
        if FLAGS.learning_rate_decay:
            current_lr = learning_rate.eval(session=sess)
        else:
            current_lr = learning_rate
        print('{:/<25}'.format('Epoch {} (learning rate {}) ({} steps)'.format(
            epoch+1, current_lr, global_step.eval(session=sess))))
        train_loss = run_epoch(sess, train, inputs, targets,
                               train_op, loss, gnorm)
        print()
        valid_accuracy = run_epoch(sess, valid, inputs, targets,
                                   tf.no_op(), accuracy, None)
        print()
        print('---Train loss:     {}'.format(train_loss))
        print('---Valid accuracy: {}'.format(valid_accuracy))
        with open(results_file, 'a') as f:
            f.write('{},{}\n'.format(train_loss, valid_accuracy))
        if (epoch+1) % 10 == 0:  # OR BEST VALID ERROR
            print('...saving', end='', flush=True)
            saver.save(sess, model_filename, global_step=epoch+1,
                       write_meta_graph=False)
            print('\r---Saved model.')
        if np.isnan(train_loss):
            print('Loss is nan, quitting')
            with open(os.path.join(results_dir, 'diverged.txt'), 'w') as f:
                f.write('Quit after {} epochs with nan loss.\n'.format(epoch+1))
                return


    print('...saving', end='', flush=True)
    saver.save(sess, model_filename+'-final', write_meta_graph=False)
    print('\r---Saved model.')

    print('{:\\^40}'.format('testing'))
    test_accuracy = run_epoch(sess, test, inputs, targets,
                              tf.no_op(), accuracy, None)
    print('{:^40}'.format(test_accuracy))
    with open(test_results, 'w') as f:
        f.write('{}'.format(test_accuracy))

if __name__ == '__main__':
    tf.app.run()
