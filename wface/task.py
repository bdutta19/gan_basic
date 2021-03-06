from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import time
import dataset
import model
from train_op import D_train_op
from train_op import G_train_op

import numpy as np
import tensorflow as tf
from tensorflow.python.platform import gfile
flags = tf.app.flags
FLAGS = flags.FLAGS

flags.DEFINE_integer("epochs", 100, "Epoch to train [25]")
flags.DEFINE_integer("steps", 100, "Epoch to train [100]")
flags.DEFINE_float("learning_rate", 0.0001, "Learning rate of for adam [0.0002]")
flags.DEFINE_float("beta1", 0.5, "Momentum term of adam [0.5]")
flags.DEFINE_integer("train_size", np.inf, "The size of train images [np.inf]")
flags.DEFINE_integer("batch_size", 64, "The size of batch images [64]")
flags.DEFINE_integer("sample_size", 64, "The size of sample images [64]")
flags.DEFINE_integer("image_height", 64, "The size of image to use (will be center cropped) [64]")
flags.DEFINE_integer("image_width", 64, "The size of image to use (will be center cropped) [64]")
flags.DEFINE_integer("image_height_org", 108, "original image height")
flags.DEFINE_integer("image_width_org", 108, "original image width")
flags.DEFINE_integer("image_depth_org", 3, "original image depth")
flags.DEFINE_integer("num_threads", 4, "number of threads using queue")

flags.DEFINE_integer("y_dim", None, "dimension of dim for y")
flags.DEFINE_integer("z_dim", 100, "dimension of dim for Z for sampling")
flags.DEFINE_integer("gc_dim", 64, "dimension of generative filters in conv layer")
flags.DEFINE_integer("dc_dim", 64, "dimension of discriminative filters in conv layer")

flags.DEFINE_string("model_name", "/media/newton/data/models/gan/wface_h_fm_gp", "model_name")
flags.DEFINE_string("data_dir", "/home/newton/source/gan_basic/face/data/face", "data dir path")
flags.DEFINE_string("sample_dir", "samples", "sample_name")
flags.DEFINE_string("checkpoint_dir", "checkpoint", "Directory name to save the checkpoints [checkpoint]")
flags.DEFINE_float('gpu_memory_fraction', 0.5, 'gpu memory fraction.')
flags.DEFINE_float('c_param', 0.01, 'discriminator clip parameters.')

flags.DEFINE_integer("data_type", 1, "1: hollywood, 2: lfw")
flags.DEFINE_bool("is_crop", True, "crop training images?")
flags.DEFINE_float('fm_rate', 0.1, 'feature matching rate.')

flags.DEFINE_float('grad_penalty_rambda', 10.0, 'Gradient penalty lambda hyperparameter')


class DCGAN_GP():
    def __init__(self, model_name, checkpoint_dir):
        self.model_name = model_name
        self.checkpoint_dir = checkpoint_dir

    def step(self, images, z):
        z_sum = tf.summary.histogram("z", z)

        self.generator = model.Generator(FLAGS.batch_size, FLAGS.gc_dim)
        self.G = self.generator.inference(z)

        # descriminator inference using true images
        self.discriminator = model.DescriminatorNoBN(FLAGS.batch_size, FLAGS.dc_dim)
        self.D1, D1_logits, D1_inter = self.discriminator.inference(images)

        # descriminator inference using sampling with G
        self.samples = self.generator.sampler(z, reuse=True)
        self.D2, D2_logits, D2_inter  = self.discriminator.inference(self.G, reuse=True)

        d1_sum = tf.summary.histogram("d1", self.D1)
        d2_sum = tf.summary.histogram("d2", self.D2)
        G_sum = tf.summary.histogram("G", self.G)

        return images, self.G, D1_logits, D2_logits, D1_inter, D2_inter, G_sum, z_sum, d1_sum, d2_sum

    def cost(self, real_data, fake_data, D1_logits, D2_logits, D1_inter, D2_inter):
        # real image loss for descriminator
        d_loss_real = tf.reduce_mean(D1_logits)
        # fake image loss for descriminator
        d_loss_fake = tf.reduce_mean(D2_logits)
        # fake image loss for generator
        g_loss = -tf.reduce_mean(D2_logits)

        # fake images loss (1) for generator with feature matching
        d1_inter = tf.reduce_mean(D1_inter, reduction_indices=(0))
        d2_inter = tf.reduce_mean(D2_inter, reduction_indices=(0))
        print("feature matching:")
        print(tf.nn.l2_loss(d1_inter - d2_inter).shape)
        fm_loss = tf.multiply(tf.nn.l2_loss(d1_inter - d2_inter), FLAGS.fm_rate)

        # summary
        d_loss_real_sum = tf.summary.scalar("d_loss_real", d_loss_real)
        d_loss_fake_sum = tf.summary.scalar("d_loss_fake", d_loss_fake)
        d_loss = -d_loss_real + d_loss_fake
        g_loss_sum = tf.summary.scalar("g_loss", g_loss)
        d_loss_sum = tf.summary.scalar("d_loss", d_loss)

        # improved wgan
        alpha = tf.random_uniform(
            shape=[FLAGS.batch_size, 1],
            minval=0.,
            maxval=1.
        )
        differences = fake_data - real_data  # disc_cost
        interpolates = real_data + (alpha * differences)
        self.D3, D3_logits, D3_inter = self.discriminator.inference(interpolates, reuse=True)
        gradients = tf.gradients(D3_logits, [interpolates])[0]
        slopes = tf.sqrt(tf.reduce_sum(tf.square(gradients), reduction_indices=[1]))
        gradient_penalty = tf.reduce_mean((slopes - 1.) ** 2)
        d_loss += FLAGS.grad_penalty_rambda * gradient_penalty

        return d_loss_real, d_loss_fake, d_loss_real_sum, d_loss_fake_sum, d_loss_sum, g_loss_sum, d_loss, g_loss, fm_loss

    def generate_images(self, z, row=8, col=8):
        images = tf.cast(tf.multiply(tf.add(self.samples, 1.0), 127.5), tf.uint8)
        print(images.get_shape())
        images = [image for image in tf.split(images, FLAGS.batch_size, axis=0)]
        rows = []
        for i in range(row):
            rows.append(tf.concat(images[col * i + 0:col * i + col], axis=2))
        image = tf.concat(rows, axis=1)
        return tf.image.encode_png(tf.squeeze(image, [0]))


class DCGAN():
    def __init__(self, model_name, checkpoint_dir):
        self.model_name = model_name
        self.checkpoint_dir = checkpoint_dir

    def step(self, images, z):
        z_sum = tf.summary.histogram("z", z)

        self.generator = model.Generator(FLAGS.batch_size, FLAGS.gc_dim)
        self.G = self.generator.inference(z)

        # descriminator inference using true images
        self.discriminator = model.Descriminator(FLAGS.batch_size, FLAGS.dc_dim)
        self.D1, D1_logits, D1_inter = self.discriminator.inference(images)

        # descriminator inference using sampling with G
        self.samples = self.generator.sampler(z, reuse=True)
        self.D2, D2_logits, D2_inter  = self.discriminator.inference(self.G, reuse=True)

        d1_sum = tf.summary.histogram("d1", self.D1)
        d2_sum = tf.summary.histogram("d2", self.D2)
        G_sum = tf.summary.histogram("G", self.G)

        return images, D1_logits, D2_logits, D1_inter, D2_inter, G_sum, z_sum, d1_sum, d2_sum

    def cost(self, D1_logits, D2_logits, D1_inter, D2_inter):
        # real image loss for descriminator
        d_loss_real = tf.reduce_mean(D1_logits)
        # fake image loss for descriminator
        d_loss_fake = tf.reduce_mean(D2_logits)
        # fake image loss for generator
        g_loss = -tf.reduce_mean(D2_logits)

        # fake images loss (1) for generator with feature matching
        d1_inter = tf.reduce_mean(D1_inter, reduction_indices=(0))
        d2_inter = tf.reduce_mean(D2_inter, reduction_indices=(0))
        print("feature matching:")
        print(tf.nn.l2_loss(d1_inter - d2_inter).shape)
        fm_loss = tf.multiply(tf.nn.l2_loss(d1_inter - d2_inter), FLAGS.fm_rate)

        # summary
        d_loss_real_sum = tf.summary.scalar("d_loss_real", d_loss_real)
        d_loss_fake_sum = tf.summary.scalar("d_loss_fake", d_loss_fake)
        d_loss = d_loss_real - d_loss_fake
        g_loss_sum = tf.summary.scalar("g_loss", g_loss)
        d_loss_sum = tf.summary.scalar("d_loss", d_loss)
        return d_loss_real, d_loss_fake, d_loss_real_sum, d_loss_fake_sum, d_loss_sum, g_loss_sum, d_loss, g_loss, fm_loss

    def generate_images(self, z, row=8, col=8):
        images = tf.cast(tf.multiply(tf.add(self.samples, 1.0), 127.5), tf.uint8)
        print(images.get_shape())
        images = [image for image in tf.split(images, FLAGS.batch_size, axis=0)]
        rows = []
        for i in range(row):
            rows.append(tf.concat(images[col * i + 0:col * i + col], axis=2))
        image = tf.concat(rows, axis=1)
        return tf.image.encode_png(tf.squeeze(image, [0]))


def train(gp=True):
    if FLAGS.data_type == 1:
        # datadir, org_height, org_width, org_depth=3, batch_size=32, threads_num=4
        datas = dataset.Dataset(FLAGS.data_dir, FLAGS.image_height_org, FLAGS.image_width_org,
                            FLAGS.image_depth_org, FLAGS.batch_size, FLAGS.num_threads, type=FLAGS.data_type, crop=FLAGS.is_crop)
    elif FLAGS.data_type == 2:
        datas = dataset.Dataset(FLAGS.data_dir, FLAGS.image_height_org, FLAGS.image_width_org,
                            FLAGS.image_depth_org, FLAGS.batch_size, FLAGS.num_threads, type=FLAGS.data_type, crop=FLAGS.is_crop)
    else:
        print("invalid data type.")
        return

    images = datas.get_inputs(FLAGS.image_height, FLAGS.image_width)

    z = tf.placeholder(tf.float32, [None, FLAGS.z_dim], name='z')

    if gp:
        dcgan = DCGAN_GP(FLAGS.model_name, FLAGS.checkpoint_dir)
        images_inf, generates, logits1, logits2, inter1, inter2, G_sum, z_sum, d1_sum, d2_sum = dcgan.step(images, z)
        d_loss_real, d_loss_fake, d_loss_real_sum, d_loss_fake_sum, d_loss_sum, g_loss_sum, d_loss, g_loss, fm_loss = dcgan.cost(
            images, generates, logits1, logits2, inter1, inter2)

        # trainable variables
        t_vars = tf.trainable_variables()
        d_vars = [var for var in t_vars if 'd_' in var.name]
        g_vars = [var for var in t_vars if 'g_' in var.name]
        # train operations
        d_optim = D_train_op(d_loss, d_vars, FLAGS.learning_rate, FLAGS.beta1)
        g_optim = G_train_op(g_loss, g_vars, FLAGS.learning_rate, FLAGS.beta1)

        # clip d parameters
        #clip_updates = [tf.assign(var, tf.clip_by_value(var, -FLAGS.c_param, FLAGS.c_param)) for var in d_vars]
    else:
        dcgan = DCGAN(FLAGS.model_name, FLAGS.checkpoint_dir)
        images_inf, logits1, logits2, inter1, inter2, G_sum, z_sum, d1_sum, d2_sum = dcgan.step(images, z)
        d_loss_real, d_loss_fake, d_loss_real_sum, d_loss_fake_sum, d_loss_sum, g_loss_sum, d_loss, g_loss, fm_loss = dcgan.cost(
            logits1, logits2, inter1, inter2)

        # trainable variables
        t_vars = tf.trainable_variables()
        d_vars = [var for var in t_vars if 'd_' in var.name]
        g_vars = [var for var in t_vars if 'g_' in var.name]
        # train operations
        d_optim = D_train_op(-d_loss, d_vars, FLAGS.learning_rate, FLAGS.beta1)
        g_optim = G_train_op(g_loss + fm_loss, g_vars, FLAGS.learning_rate, FLAGS.beta1)

        # clip d parameters
        clip_updates = [tf.assign(var, tf.clip_by_value(var, -FLAGS.c_param, FLAGS.c_param)) for var in d_vars]

    # saver
    saver = tf.train.Saver()

    # sampling from z
    generate_images = dcgan.generate_images(z, 8, 8)

    # initialization
    init_op = tf.global_variables_initializer()
    gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=FLAGS.gpu_memory_fraction)
    sess = tf.Session(config=tf.ConfigProto(
        allow_soft_placement=True,
        gpu_options=gpu_options))
    writer = tf.summary.FileWriter("./logs", sess.graph_def)

    # run
    sess.run(init_op)

    # load trained parameters
    model_dir = os.path.join(FLAGS.model_name, FLAGS.checkpoint_dir)
    ckpt = tf.train.get_checkpoint_state(model_dir)
    if ckpt and ckpt.model_checkpoint_path:
        saver.restore(sess, ckpt.model_checkpoint_path)
        print("Model restored.")
    else:
        print("No checkpoint file found")

    # summary
    g_sum = tf.summary.merge([z_sum, d2_sum, G_sum, d_loss_fake_sum, g_loss_sum])
    d_sum = tf.summary.merge([z_sum, d1_sum, d_loss_real_sum, d_loss_sum])

    coord = tf.train.Coordinator()
    threads = tf.train.start_queue_runners(sess=sess, coord=coord)

    counter = 1
    start_time = time.time()

    for epoch in xrange(FLAGS.epochs):
        for idx in xrange(0, int(datas.batch_idxs)):
            batch_z = np.random.uniform(-1, 1, [FLAGS.batch_size, FLAGS.z_dim]).astype(np.float32)

            # D optimization
            for _ in xrange(5):
                if not gp:
                    sess.run([clip_updates], {z: batch_z})
                images_inf_eval, _, summary_str = sess.run([images_inf, d_optim, d_sum], {z: batch_z})
                writer.add_summary(summary_str, counter)

            # twice G optimization
            # _, summary_str = sess.run([g_optim, g_sum], {z: batch_z})
            # writer.add_summary(summary_str, counter)
            _, summary_str = sess.run([g_optim, g_sum], {z: batch_z})
            writer.add_summary(summary_str, counter)

            if gp:
                errD = sess.run(d_loss, {z: batch_z})
                errG = sess.run(g_loss, {z: batch_z})
                print("epochs: %02d %04d/%04d time: %4.4f, d_loss: %.8f, g_loss: %.8f: sample count: %d" % (
                    epoch, idx, FLAGS.steps, time.time() - start_time, errD, errG, counter))
            else:
                errD_fake = sess.run(d_loss_fake, {z: batch_z})
                errD_real = sess.run(d_loss_real)
                errG = sess.run(g_loss, {z: batch_z})
                print("epochs: %02d %04d/%04d time: %4.4f, d_loss: %.8f (F:%.8f, R:%.8f), g_loss: %.8f: : sample count: %d" % (
                    epoch, idx, FLAGS.steps, time.time() - start_time, errD_fake + errD_real, errD_fake, errD_real, errG, counter))

            if np.mod(counter, 100) == 1:
                print("generate samples.")
                generated_image_eval = sess.run(generate_images, {z: batch_z})
                out_dir = os.path.join(FLAGS.model_name, FLAGS.sample_dir)
                if not gfile.Exists(out_dir):
                    gfile.MakeDirs(out_dir)
                filename = os.path.join(out_dir, 'out_%05d.png' % counter)
                with open(filename, 'wb') as f:
                    f.write(generated_image_eval)
            counter += 1
        if np.mod(epoch, 10) == 0:
            out_dir = os.path.join(FLAGS.model_name, FLAGS.checkpoint_dir)
            if not gfile.Exists(out_dir):
                gfile.MakeDirs(out_dir)
            out_path = os.path.join(out_dir, 'model.ckpt')
            saver.save(sess, out_path, global_step=epoch)
    coord.request_stop()
    coord.join(threads)
    sess.close()


def main(_):
    print("face DCGANs.")
    train()


if __name__ == '__main__':
    tf.app.run()