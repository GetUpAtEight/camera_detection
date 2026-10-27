package com.demo.tamper.repository;

import com.demo.tamper.model.entity.MediaFile;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface MediaFileRepository extends JpaRepository<MediaFile, String> {

    List<MediaFile> findAllByOrderByCreatedAtDesc();
}
