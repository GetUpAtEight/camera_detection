package com.demo.tamper.repository;

import com.demo.tamper.model.entity.DetectionTask;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DetectionTaskRepository extends JpaRepository<DetectionTask, String> {

    List<DetectionTask> findAllByOrderByCreatedAtDesc();

    List<DetectionTask> findByMediaIdOrderByCreatedAtDesc(String mediaId);
}
